"""
generate_puttview_fixture.py

Generates synthetic PuttView-style green map images for testing.
Mimics the layout seen in real PuttView books:
  - Graph paper background with axis labels (yards)
  - Irregular oval green boundary
  - Numeric elevation values at every 3-yard grid point inside the green
  - Small break arrows at each grid point
  - Optional noise and rotation for robustness testing

Usage:
    python tests/generate_puttview_fixture.py          # saves to tests/fixtures/
    python tests/generate_puttview_fixture.py --show   # also opens image for preview

Returns (when imported):
    generate_fixture() -> (image: np.ndarray, ground_truth: dict)
"""

import argparse
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixture config
# ---------------------------------------------------------------------------

@dataclass
class FixtureConfig:
    dpi: int = 600
    grid_spacing_yards: int = 3
    green_width_yards: int = 30       # approximate green width
    green_height_yards: int = 24      # approximate green height
    margin_yards: int = 5             # space around green to edge of image
    elevation_min: float = 0.5
    elevation_max: float = 7.5
    rotation_deg: float = 0.0         # simulate scan rotation
    noise_level: float = 0.0          # 0.0 = clean, 1.0 = heavy noise
    seed: int = 42


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

GRAPH_PAPER_COLOR = (245, 248, 255)   # near-white with slight blue tint
GRID_LINE_COLOR   = (180, 200, 220)   # light blue grid lines
GREEN_FILL_COLOR  = (180, 225, 180)   # clearly green inside boundary (saturation visible in HSV)
GREEN_LINE_COLOR  = (30, 100, 30)     # dark green boundary
AXIS_LABEL_COLOR  = (60, 60, 60)
ELEVATION_COLOR   = (20, 20, 20)      # near-black numbers
ARROW_COLOR       = (80, 80, 80)


def yards_to_px(yards: float, px_per_yard: float) -> int:
    return int(round(yards * px_per_yard))


def build_green_polygon(
    cx: float, cy: float,
    rx: float, ry: float,
    num_points: int = 32,
    irregularity: float = 0.15,
    rng: random.Random = None,
) -> list[tuple[int, int]]:
    """
    Builds an irregular oval polygon approximating a golf green shape.
    cx, cy = center in pixels; rx, ry = semi-axes in pixels.
    """
    if rng is None:
        rng = random.Random(42)

    points = []
    for i in range(num_points):
        angle = 2 * math.pi * i / num_points
        r_vary = 1.0 + rng.uniform(-irregularity, irregularity)
        x = cx + rx * r_vary * math.cos(angle)
        y = cy + ry * r_vary * math.sin(angle)
        points.append((int(x), int(y)))
    return points


def draw_arrow(draw: ImageDraw.ImageDraw, cx: int, cy: int, angle_deg: float, size: int):
    """Draws a small directional arrow at (cx, cy)."""
    angle_rad = math.radians(angle_deg)
    dx = math.cos(angle_rad) * size
    dy = math.sin(angle_rad) * size

    x0, y0 = cx - dx * 0.5, cy - dy * 0.5
    x1, y1 = cx + dx * 0.5, cy + dy * 0.5

    draw.line([(x0, y0), (x1, y1)], fill=ARROW_COLOR, width=max(1, size // 6))

    # Arrowhead
    head_len = size * 0.35
    left_angle = angle_rad + math.radians(150)
    right_angle = angle_rad - math.radians(150)
    draw.line(
        [(x1, y1), (x1 + math.cos(left_angle) * head_len, y1 + math.sin(left_angle) * head_len)],
        fill=ARROW_COLOR, width=max(1, size // 6)
    )
    draw.line(
        [(x1, y1), (x1 + math.cos(right_angle) * head_len, y1 + math.sin(right_angle) * head_len)],
        fill=ARROW_COLOR, width=max(1, size // 6)
    )


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_fixture(config: Optional[FixtureConfig] = None) -> tuple[np.ndarray, dict]:
    """
    Generates a synthetic PuttView numeric grid image.

    Returns:
        image: np.ndarray (BGR, uint8) — the generated image
        ground_truth: dict with keys:
            - hole: int
            - grid_spacing_yards: int
            - px_per_yard: float
            - origin_px: (x, y) pixel of (0,0) yard coordinate
            - points: list of {x_yards, y_yards, elevation_ft, px, py}
    """
    if config is None:
        config = FixtureConfig()

    rng = random.Random(config.seed)
    np.random.seed(config.seed)

    # Pixel density — scale relative to 600 DPI reference
    px_per_yard = (config.dpi / 600.0) * 75.0   # 75 px/yard at 600 DPI (3/8" = 3yds)

    total_yards_w = config.green_width_yards + config.margin_yards * 2
    total_yards_h = config.green_height_yards + config.margin_yards * 2
    img_w = yards_to_px(total_yards_w, px_per_yard)
    img_h = yards_to_px(total_yards_h, px_per_yard)

    # Origin = top-left of the content area (0,0 yards)
    origin_x = yards_to_px(config.margin_yards, px_per_yard)
    origin_y = yards_to_px(config.margin_yards, px_per_yard)

    img = Image.new("RGB", (img_w, img_h), GRAPH_PAPER_COLOR)
    draw = ImageDraw.Draw(img)

    # --- Graph paper grid lines ---
    fine_spacing_px = yards_to_px(1, px_per_yard)
    for x in range(0, img_w, fine_spacing_px):
        draw.line([(x, 0), (x, img_h)], fill=GRID_LINE_COLOR, width=1)
    for y in range(0, img_h, fine_spacing_px):
        draw.line([(0, y), (img_w, y)], fill=GRID_LINE_COLOR, width=1)

    # --- Axis labels (every 5 yards) ---
    font_size = max(12, yards_to_px(0.5, px_per_yard))
    try:
        font_label = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
        font_elev  = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", max(10, font_size - 4))
    except Exception:
        font_label = ImageFont.load_default()
        font_elev  = font_label

    for yard in range(0, int(total_yards_w), 5):
        px = origin_x + yards_to_px(yard, px_per_yard)
        draw.text((px, 4), str(yard), fill=AXIS_LABEL_COLOR, font=font_label)
    for yard in range(0, int(total_yards_h), 5):
        py = origin_y + yards_to_px(yard, px_per_yard)
        draw.text((4, py), str(yard), fill=AXIS_LABEL_COLOR, font=font_label)

    # --- Green boundary polygon ---
    green_cx = origin_x + yards_to_px(config.green_width_yards / 2, px_per_yard)
    green_cy = origin_y + yards_to_px(config.green_height_yards / 2, px_per_yard)
    green_rx = yards_to_px(config.green_width_yards / 2 * 0.85, px_per_yard)
    green_ry = yards_to_px(config.green_height_yards / 2 * 0.85, px_per_yard)

    green_poly = build_green_polygon(green_cx, green_cy, green_rx, green_ry, rng=rng)
    draw.polygon(green_poly, fill=GREEN_FILL_COLOR)
    draw.polygon(green_poly, outline=GREEN_LINE_COLOR, width=max(2, yards_to_px(0.08, px_per_yard)))

    # Build green mask for point-in-polygon checks
    mask_img = Image.new("L", (img_w, img_h), 0)
    ImageDraw.Draw(mask_img).polygon(green_poly, fill=255)
    green_mask = np.array(mask_img)

    # --- Generate elevation values on a smooth surface ---
    # Use a tilted plane + gaussian bump to simulate realistic green undulation
    grid_w = int(config.green_width_yards / config.grid_spacing_yards) + 3
    grid_h = int(config.green_height_yards / config.grid_spacing_yards) + 3

    def elevation_at(x_y: float, y_y: float) -> float:
        # Normalize to 0-1 range
        nx = x_y / config.green_width_yards
        ny = y_y / config.green_height_yards
        # Tilted plane
        base = config.elevation_min + (config.elevation_max - config.elevation_min) * (0.3 * nx + 0.7 * ny)
        # Gaussian ridge
        bump = 1.5 * math.exp(-((nx - 0.4) ** 2 + (ny - 0.3) ** 2) / 0.1)
        return round(min(config.elevation_max, max(config.elevation_min, base + bump)), 1)

    # --- Place elevation numbers at each 3-yard grid point ---
    points = []
    cell_px = yards_to_px(config.grid_spacing_yards, px_per_yard)

    for gx in range(-1, grid_w + 1):
        for gy in range(-1, grid_h + 1):
            x_yards = gx * config.grid_spacing_yards
            y_yards = gy * config.grid_spacing_yards

            px = origin_x + yards_to_px(x_yards, px_per_yard)
            py = origin_y + yards_to_px(y_yards, px_per_yard)

            # Skip if outside image
            if px < 0 or px >= img_w or py < 0 or py >= img_h:
                continue

            elev = elevation_at(x_yards, y_yards)
            elev_text = f"{elev:.1f}"
            inside_green = green_mask[py, px] > 0

            # Draw number (inside green in black, outside in gray)
            text_color = ELEVATION_COLOR if inside_green else (180, 180, 180)
            # Place number slightly above center of cell
            draw.text((px - cell_px // 4, py - cell_px // 3), elev_text,
                      fill=text_color, font=font_elev)

            if inside_green:
                # Draw break arrow pointing downhill (away from bump center)
                arrow_angle = math.degrees(math.atan2(
                    y_yards - config.green_height_yards * 0.3,
                    x_yards - config.green_width_yards * 0.4
                ))
                draw_arrow(draw, px, py + cell_px // 4, arrow_angle, cell_px // 3)

                points.append({
                    "x_yards": x_yards,
                    "y_yards": y_yards,
                    "elevation_ft": elev,
                    "px": px,
                    "py": py,
                })

    # --- Optional noise ---
    img_array = np.array(img, dtype=np.float32)
    if config.noise_level > 0:
        noise = np.random.normal(0, config.noise_level * 15, img_array.shape)
        img_array = np.clip(img_array + noise, 0, 255)
    img_array = img_array.astype(np.uint8)

    # --- Optional rotation ---
    if config.rotation_deg != 0.0:
        center = (img_w // 2, img_h // 2)
        M = cv2.getRotationMatrix2D(center, config.rotation_deg, 1.0)
        img_array = cv2.warpAffine(img_array, M, (img_w, img_h),
                                   borderValue=GRAPH_PAPER_COLOR[::-1])  # BGR

    # Convert RGB → BGR for OpenCV compatibility
    img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

    ground_truth = {
        "hole": 1,
        "grid_spacing_yards": config.grid_spacing_yards,
        "px_per_yard": px_per_yard,
        "origin_px": (origin_x, origin_y),
        "image_size": (img_w, img_h),
        "green_center_px": (green_cx, green_cy),
        "green_rx_px": green_rx,
        "green_ry_px": green_ry,
        "points": points,
    }

    return img_bgr, ground_truth


def save_fixtures(output_dir: Path = FIXTURES_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)

    configs = {
        "puttview_clean.png": FixtureConfig(),
        "puttview_noisy.png": FixtureConfig(noise_level=0.4, seed=99),
        "puttview_rotated.png": FixtureConfig(rotation_deg=2.5, seed=7),
    }

    saved = {}
    for filename, cfg in configs.items():
        img, gt = generate_fixture(cfg)
        out_path = output_dir / filename
        cv2.imwrite(str(out_path), img)

        import json
        gt_serializable = {k: v for k, v in gt.items() if k != "points"}
        gt_serializable["points"] = gt["points"]
        gt_path = output_dir / filename.replace(".png", "_gt.json")
        with open(gt_path, "w") as f:
            json.dump(gt_serializable, f, indent=2)

        saved[filename] = {"image": out_path, "ground_truth": gt_path, "num_points": len(gt["points"])}
        print(f"  {filename}: {len(gt['points'])} green points")

    return saved


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", action="store_true", help="Display generated images")
    args = parser.parse_args()

    print("Generating PuttView fixtures...")
    saved = save_fixtures()

    if args.show:
        for name, paths in saved.items():
            img = cv2.imread(str(paths["image"]))
            cv2.imshow(name, cv2.resize(img, (800, 600)))
        cv2.waitKey(0)
        cv2.destroyAllWindows()
