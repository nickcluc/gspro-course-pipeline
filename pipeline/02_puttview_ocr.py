"""
02_puttview_ocr.py — PuttView Book OCR Pipeline

Processes scanned PuttView green map pages and extracts numeric elevation
values into per-hole JSON grids.

Each scanned page contains two stacked panels:
  - Top: numeric grid (elevation values + break arrows at every 3-yard point)
  - Bottom: color heat map (used for visual validation only)

Usage:
    python pipeline/02_puttview_ocr.py --course tedesco --scans-dir path/to/scans/
    python pipeline/02_puttview_ocr.py --course tedesco --page path/to/hole_01.png --hole 1

Input:  Scanned PNG pages at 300+ DPI (600 DPI recommended)
Output: courses/<slug>/data/processed/grids/hole_XX_green.json

Dependencies:
    pip install opencv-python pytesseract numpy scipy
    brew install tesseract
"""

import argparse
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from scipy.ndimage import label

REPO_ROOT = Path(__file__).resolve().parent.parent
TESSERACT_CONFIG = "--psm 7 -c tessedit_char_whitelist=0123456789."


# ---------------------------------------------------------------------------
# Image loading and preprocessing
# ---------------------------------------------------------------------------

def load_image(path: Path) -> np.ndarray:
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"Could not load image: {path}")
    return img


def crop_to_numeric_panel(image: np.ndarray) -> np.ndarray:
    """
    PuttView pages have two stacked panels: numeric grid (top) and heat map (bottom).
    Returns just the top panel.

    If the image is already a single panel (height < 1.3x width), returns as-is.
    """
    h, w = image.shape[:2]
    if h > w * 1.3:
        # Full page — take top half
        return image[:h // 2, :]
    return image


def to_grayscale(image: np.ndarray) -> np.ndarray:
    if len(image.shape) == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def enhance_for_ocr(gray: np.ndarray) -> np.ndarray:
    """
    Prepares a grayscale image for tesseract:
    - CLAHE contrast enhancement
    - Adaptive threshold to binary
    - Mild denoising
    """
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    binary = cv2.adaptiveThreshold(
        enhanced, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=15,
        C=8
    )
    denoised = cv2.medianBlur(binary, 3)
    return denoised


# ---------------------------------------------------------------------------
# Green boundary detection
# ---------------------------------------------------------------------------

def detect_green_boundary(image: np.ndarray) -> tuple[np.ndarray, list]:
    """
    Detects the green boundary as the largest closed contour in the image.

    Returns:
        mask: binary mask (255 = inside green)
        contour: the green boundary contour points
    """
    h, w = image.shape[:2]

    # Use HSV color segmentation to find the green-filled area.
    # PuttView greens are filled with a clearly green hue (H ~35-85 in OpenCV's 0-179 scale).
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    green_mask_color = cv2.inRange(hsv,
        np.array([30, 15, 100]),   # lower: hue=60°, low sat, bright
        np.array([90, 255, 255])   # upper: hue=180°, any sat, any brightness
    )

    # Close gaps and fill the interior solidly
    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
    closed = cv2.morphologyEx(green_mask_color, cv2.MORPH_CLOSE, kernel_close)

    kernel_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    filled = cv2.dilate(closed, kernel_dilate, iterations=2)

    # Find contours of the filled green region
    contours, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        raise ValueError("No green region found — check scan quality or color balance")

    # Exclude image-boundary-touching artifacts
    edge_margin = min(h, w) * 0.02
    def touches_edge(c):
        x, y, cw, ch = cv2.boundingRect(c)
        return (x < edge_margin or y < edge_margin or
                x + cw > w - edge_margin or y + ch > h - edge_margin)

    min_area = (h * w) * 0.03
    candidates = [c for c in contours if cv2.contourArea(c) > min_area and not touches_edge(c)]

    if not candidates:
        candidates = [max(contours, key=cv2.contourArea)]

    # Pick largest candidate (the green is the dominant colored region)
    green_contour = max(candidates, key=cv2.contourArea)

    # Build mask
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.drawContours(mask, [green_contour], -1, 255, cv2.FILLED)

    return mask, green_contour


# ---------------------------------------------------------------------------
# Scale detection
# ---------------------------------------------------------------------------

def estimate_scale_from_axis_labels(image: np.ndarray) -> float | None:
    """
    Attempts to determine pixels-per-yard by finding axis labels along the
    top or left margin of the numeric grid.

    PuttView labels axes at 0, 5, 10, 15, 20, 25, 30, 35 yards.
    Returns px_per_yard, or None if detection fails.
    """
    h, w = image.shape[:2]
    margin = int(min(h, w) * 0.12)

    # Look for axis label region along top margin
    top_margin = image[:margin, :]
    gray = to_grayscale(top_margin)
    enhanced = enhance_for_ocr(gray)

    # OCR the top margin to find yard labels
    data = pytesseract.image_to_data(
        enhanced,
        config="--psm 11 -c tessedit_char_whitelist=0123456789",
        output_type=pytesseract.Output.DICT
    )

    # Extract (x_position, value) pairs for valid numeric labels
    label_positions = []
    for i, text in enumerate(data["text"]):
        text = text.strip()
        if text.isdigit() and int(text) % 5 == 0:
            x = data["left"][i] + data["width"][i] // 2
            label_positions.append((x, int(text)))

    if len(label_positions) < 2:
        return None

    # Sort by position and compute px/yard from consecutive pairs
    label_positions.sort(key=lambda p: p[0])
    scales = []
    for i in range(1, len(label_positions)):
        dx = label_positions[i][0] - label_positions[i - 1][0]
        dy = label_positions[i][1] - label_positions[i - 1][1]
        if dy > 0:
            scales.append(dx / dy)

    return float(np.median(scales)) if scales else None


def fallback_scale_from_green(green_contour, green_width_yards: float = 28.0) -> float:
    """
    Estimates px/yard from the green contour width and known average green size.
    Used when axis label detection fails.
    """
    x, y, w, h = cv2.boundingRect(green_contour)
    return w / green_width_yards


# ---------------------------------------------------------------------------
# Grid cell extraction
# ---------------------------------------------------------------------------

def extract_cell_image(
    image: np.ndarray,
    cx: int, cy: int,
    cell_px: int,
    padding_fraction: float = 0.25,
) -> np.ndarray:
    """
    Extracts a cell region centered at (cx, cy).
    Returns the upper portion of the cell where the elevation number lives.
    """
    h, w = image.shape[:2]
    half = cell_px // 2

    # Crop to cell bounds
    x0 = max(0, cx - half)
    x1 = min(w, cx + half)
    y0 = max(0, cy - half)
    y1 = min(h, cy + half)
    cell = image[y0:y1, x0:x1]

    if cell.size == 0:
        return cell

    # Take just the upper-center portion (where number sits, above the arrow)
    ch, cw = cell.shape[:2]
    num_region = cell[:int(ch * 0.55), int(cw * 0.1):int(cw * 0.9)]
    return num_region


def ocr_elevation(cell_image: np.ndarray, min_scale: int = 3) -> float | None:
    """
    OCRs a single cell image and returns the elevation as a float.
    Returns None if no valid number is found.

    Tries multiple preprocessing approaches and returns the most confident result.
    """
    if cell_image is None or cell_image.size == 0:
        return None

    gray = to_grayscale(cell_image)

    # Scale up small cells — tesseract works better on larger text
    h, w = gray.shape
    if h < 30:
        scale = max(min_scale, 30 // max(h, 1))
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

    results = []
    for thresh_method in ["adaptive", "otsu"]:
        if thresh_method == "adaptive":
            binary = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, 11, 5
            )
        else:
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Try both normal and inverted (handles dark-on-light and light-on-dark)
        for img in [binary, cv2.bitwise_not(binary)]:
            # Add white border for tesseract
            padded = cv2.copyMakeBorder(img, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
            text = pytesseract.image_to_string(padded, config=TESSERACT_CONFIG).strip()
            parsed = _parse_elevation_text(text)
            if parsed is not None:
                results.append(parsed)

    if not results:
        return None

    value = float(np.median(results))
    return value if 0.0 <= value <= 10.0 else None


def _parse_elevation_text(text: str) -> float | None:
    """
    Parses an OCR result string into an elevation float.
    Handles common OCR errors (e.g. 'O' for '0', missing decimal).
    """
    if not text:
        return None

    # Clean common OCR substitutions
    cleaned = (
        text.strip()
        .replace("O", "0")
        .replace("o", "0")
        .replace("l", "1")
        .replace("I", "1")
        .replace(",", ".")
        .replace(" ", "")
    )

    # Extract first valid float-like pattern
    match = re.search(r"\d+\.?\d*", cleaned)
    if not match:
        return None

    try:
        value = float(match.group())
    except ValueError:
        return None

    # Sanity check: PuttView elevations are 0–10 feet
    if 0.0 <= value <= 10.0:
        return value
    return None


# ---------------------------------------------------------------------------
# Full page processor
# ---------------------------------------------------------------------------

def process_page(
    image_path: Path,
    hole_num: int,
    px_per_yard: float | None = None,
    grid_spacing_yards: int = 3,
    green_width_yards: float = 28.0,
) -> dict:
    """
    Full pipeline for one scanned PuttView page.

    Returns a dict:
    {
        "hole": int,
        "grid_spacing_yards": int,
        "points": [{"x": float, "y": float, "elevation_ft": float}, ...]
    }
    """
    print(f"\nProcessing hole {hole_num}: {image_path.name}")

    image = load_image(image_path)
    numeric_panel = crop_to_numeric_panel(image)

    print("  Detecting green boundary...")
    green_mask, green_contour = detect_green_boundary(numeric_panel)

    green_area_px = cv2.countNonZero(green_mask)
    print(f"  Green area: {green_area_px:,} px²")

    # Determine scale
    if px_per_yard is None:
        print("  Estimating scale from axis labels...")
        px_per_yard = estimate_scale_from_axis_labels(numeric_panel)

    if px_per_yard is None:
        print("  Axis label detection failed, falling back to green size estimate")
        px_per_yard = fallback_scale_from_green(green_contour, green_width_yards)

    print(f"  Scale: {px_per_yard:.1f} px/yard")

    cell_px = int(round(px_per_yard * grid_spacing_yards))
    print(f"  Cell size: {cell_px}px ({grid_spacing_yards} yards)")

    # Determine grid origin from green bounding box
    gx, gy, gw, gh = cv2.boundingRect(green_contour)
    # Start grid slightly before the green bounding box, on a clean multiple
    origin_x = (gx // cell_px) * cell_px
    origin_y = (gy // cell_px) * cell_px

    h, w = numeric_panel.shape[:2]
    gray_panel = to_grayscale(numeric_panel)

    # Sample grid points
    points = []
    total_cells = 0
    valid_cells = 0

    for row in range(0, h + cell_px, cell_px):
        for col in range(0, w + cell_px, cell_px):
            cx = origin_x + col
            cy = origin_y + row

            if cx >= w or cy >= h:
                continue

            # Only process cells whose center is inside the green
            if green_mask[cy, cx] == 0:
                continue

            total_cells += 1
            cell_img = extract_cell_image(gray_panel, cx, cy, cell_px)
            elev = ocr_elevation(cell_img)

            if elev is not None:
                valid_cells += 1
                # Convert pixel position to yards
                x_yards = (cx - origin_x) / px_per_yard
                y_yards = (cy - origin_y) / px_per_yard
                points.append({
                    "x": round(x_yards, 1),
                    "y": round(y_yards, 1),
                    "elevation_ft": elev
                })

    print(f"  OCR: {valid_cells}/{total_cells} cells read ({valid_cells/max(total_cells,1)*100:.0f}%)")

    # The green's edge, in the same yards frame as the points (x right, y down the page)
    edge = cv2.approxPolyDP(green_contour, 0.3 * px_per_yard, True).reshape(-1, 2)
    outline = [[round((x - origin_x) / px_per_yard, 2), round((y - origin_y) / px_per_yard, 2)] for x, y in edge]

    return {
        "hole": hole_num,
        "grid_spacing_yards": grid_spacing_yards,
        "px_per_yard": round(px_per_yard, 2),
        "outline_yards": outline,
        "points": points
    }


def save_result(result: dict, output_dir: Path, hole_num: int):
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"hole_{hole_num:02d}_green.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"  Saved: {out_path} ({len(result['points'])} points)")
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def get_output_dir(slug: str) -> Path:
    return REPO_ROOT / "courses" / slug / "data" / "processed" / "grids"


def main():
    parser = argparse.ArgumentParser(description="OCR PuttView green map scans")
    parser.add_argument("--course", default="tedesco")
    parser.add_argument("--scans-dir", type=Path, help="Directory of scanned PNG pages")
    parser.add_argument("--page", type=Path, help="Single page to process")
    parser.add_argument("--hole", type=int, help="Hole number (required with --page)")
    parser.add_argument("--px-per-yard", type=float, default=None,
                        help="Override scale (pixels per yard). Auto-detected if not set.")
    args = parser.parse_args()

    output_dir = get_output_dir(args.course)

    if args.page:
        if not args.hole:
            print("--hole is required with --page")
            sys.exit(1)
        result = process_page(args.page, args.hole, args.px_per_yard)
        save_result(result, output_dir, args.hole)

    elif args.scans_dir:
        pages = sorted(args.scans_dir.glob("*.png"))
        if not pages:
            print(f"No PNG files found in {args.scans_dir}")
            sys.exit(1)
        for i, page in enumerate(pages, start=1):
            result = process_page(page, i, args.px_per_yard)
            save_result(result, output_dir, i)

    else:
        print("Provide --page or --scans-dir")
        sys.exit(1)


if __name__ == "__main__":
    main()
