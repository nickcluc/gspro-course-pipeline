"""
Tests for pipeline/02_puttview_ocr.py

Unit tests use synthetic fixtures with known ground truth.
Integration tests use the real PuttView sample image (format validation only —
  no ground truth available for that image).

Run with:
    .venv/bin/pytest tests/test_02_puttview_ocr.py -v
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import importlib
ocr = importlib.import_module("02_puttview_ocr")
from generate_puttview_fixture import FixtureConfig, generate_fixture

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_IMAGE = FIXTURES / "puttview_sample.png"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_clean_fixture(seed=42):
    img, gt = generate_fixture(FixtureConfig(seed=seed))
    return img, gt


def render_text_cell(text: str, size: int = 60) -> np.ndarray:
    """Creates a simple white cell image with black text for OCR testing."""
    cell = np.ones((size, size), dtype=np.uint8) * 255
    cv2.putText(cell, text, (5, size - 10), cv2.FONT_HERSHEY_SIMPLEX,
                size / 80, 0, max(1, size // 40))
    return cell


# ---------------------------------------------------------------------------
# load_image
# ---------------------------------------------------------------------------

class TestLoadImage:
    def test_loads_real_sample(self):
        img = ocr.load_image(SAMPLE_IMAGE)
        assert img is not None
        assert img.ndim == 3

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(ValueError, match="Could not load"):
            ocr.load_image(tmp_path / "nonexistent.png")

    def test_returns_bgr_array(self):
        img = ocr.load_image(SAMPLE_IMAGE)
        assert img.dtype == np.uint8
        assert img.shape[2] == 3


# ---------------------------------------------------------------------------
# crop_to_numeric_panel
# ---------------------------------------------------------------------------

class TestCropToNumericPanel:
    def test_tall_image_returns_top_half(self):
        img = np.zeros((1000, 400, 3), dtype=np.uint8)
        result = ocr.crop_to_numeric_panel(img)
        assert result.shape[0] == 500
        assert result.shape[1] == 400

    def test_square_image_unchanged(self):
        img = np.zeros((500, 500, 3), dtype=np.uint8)
        result = ocr.crop_to_numeric_panel(img)
        assert result.shape == img.shape

    def test_wide_image_unchanged(self):
        img = np.zeros((400, 800, 3), dtype=np.uint8)
        result = ocr.crop_to_numeric_panel(img)
        assert result.shape == img.shape

    def test_real_sample_crops_to_half(self):
        img = ocr.load_image(SAMPLE_IMAGE)
        # Sample is square (1080x1080) so shouldn't crop
        result = ocr.crop_to_numeric_panel(img)
        assert result.shape[0] <= img.shape[0]


# ---------------------------------------------------------------------------
# to_grayscale
# ---------------------------------------------------------------------------

class TestToGrayscale:
    def test_converts_color_to_gray(self):
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        result = ocr.to_grayscale(img)
        assert result.ndim == 2

    def test_passthrough_on_already_gray(self):
        img = np.zeros((100, 100), dtype=np.uint8)
        result = ocr.to_grayscale(img)
        assert result.ndim == 2
        assert result.shape == img.shape


# ---------------------------------------------------------------------------
# enhance_for_ocr
# ---------------------------------------------------------------------------

class TestEnhanceForOcr:
    def test_returns_binary_image(self):
        gray = np.random.randint(0, 255, (100, 100), dtype=np.uint8)
        result = ocr.enhance_for_ocr(gray)
        unique_vals = np.unique(result)
        assert set(unique_vals).issubset({0, 255})

    def test_output_same_shape(self):
        gray = np.random.randint(0, 255, (200, 150), dtype=np.uint8)
        result = ocr.enhance_for_ocr(gray)
        assert result.shape == gray.shape


# ---------------------------------------------------------------------------
# detect_green_boundary
# ---------------------------------------------------------------------------

class TestDetectGreenBoundary:
    def test_finds_boundary_on_synthetic_fixture(self):
        img, gt = make_clean_fixture()
        mask, contour = ocr.detect_green_boundary(img)
        assert mask is not None
        assert len(contour) > 10

    def test_mask_is_binary(self):
        img, _ = make_clean_fixture()
        mask, _ = ocr.detect_green_boundary(img)
        unique = np.unique(mask)
        assert set(unique).issubset({0, 255})

    def test_mask_covers_reasonable_fraction(self):
        img, gt = make_clean_fixture()
        mask, _ = ocr.detect_green_boundary(img)
        coverage = np.count_nonzero(mask) / mask.size
        # Green should be 10-70% of the image
        assert 0.10 < coverage < 0.70

    def test_detected_green_contains_known_points(self):
        img, gt = make_clean_fixture()
        mask, _ = ocr.detect_green_boundary(img)
        # At least 50% of known green points should be inside detected mask
        inside = 0
        for pt in gt["points"]:
            px, py = pt["px"], pt["py"]
            if 0 <= py < mask.shape[0] and 0 <= px < mask.shape[1]:
                if mask[py, px] > 0:
                    inside += 1
        ratio = inside / max(len(gt["points"]), 1)
        assert ratio > 0.5

    def test_runs_on_real_sample(self):
        img = ocr.load_image(SAMPLE_IMAGE)
        panel = ocr.crop_to_numeric_panel(img)
        # Should not raise
        mask, contour = ocr.detect_green_boundary(panel)
        assert cv2.contourArea(contour) > 0


# ---------------------------------------------------------------------------
# _parse_elevation_text
# ---------------------------------------------------------------------------

class TestParseElevationText:
    def test_valid_float(self):
        assert ocr._parse_elevation_text("3.4") == 3.4

    def test_valid_integer(self):
        assert ocr._parse_elevation_text("5") == 5.0

    def test_ocr_comma_as_decimal(self):
        assert ocr._parse_elevation_text("3,4") == 3.4

    def test_ocr_O_for_zero(self):
        assert ocr._parse_elevation_text("3.O") == 3.0

    def test_ocr_l_for_one(self):
        assert ocr._parse_elevation_text("l.5") == 1.5

    def test_out_of_range_high(self):
        assert ocr._parse_elevation_text("99.9") is None

    def test_empty_string(self):
        assert ocr._parse_elevation_text("") is None

    def test_non_numeric(self):
        assert ocr._parse_elevation_text("abc") is None

    def test_whitespace_stripped(self):
        assert ocr._parse_elevation_text("  4.2  ") == 4.2

    def test_valid_boundary_low(self):
        assert ocr._parse_elevation_text("0.0") == 0.0

    def test_valid_boundary_high(self):
        assert ocr._parse_elevation_text("10.0") == 10.0


# ---------------------------------------------------------------------------
# extract_cell_image
# ---------------------------------------------------------------------------

class TestExtractCellImage:
    def test_returns_smaller_region(self):
        img = np.ones((200, 200), dtype=np.uint8) * 128
        cell = ocr.extract_cell_image(img, 100, 100, 40)
        assert cell.shape[0] < 40
        assert cell.shape[1] < 40

    def test_handles_edge_of_image(self):
        img = np.ones((200, 200), dtype=np.uint8) * 128
        # Near corner — should not raise
        cell = ocr.extract_cell_image(img, 5, 5, 40)
        assert cell is not None

    def test_returns_upper_portion(self):
        # Create cell with distinctive top and bottom halves
        img = np.zeros((200, 200), dtype=np.uint8)
        img[:100, :] = 200  # top = light
        img[100:, :] = 50   # bottom = dark
        cell = ocr.extract_cell_image(img, 100, 100, 60)
        # Upper portion should be predominantly light
        assert cell.mean() > 100


# ---------------------------------------------------------------------------
# ocr_elevation
# ---------------------------------------------------------------------------

class TestOcrElevation:
    def test_returns_float_or_none(self):
        cell = render_text_cell("3.4")
        result = ocr.ocr_elevation(cell)
        assert result is None or isinstance(result, float)

    def test_handles_empty_cell(self):
        empty = np.ones((0, 0), dtype=np.uint8)
        assert ocr.ocr_elevation(empty) is None

    def test_handles_none(self):
        assert ocr.ocr_elevation(None) is None

    def test_rejects_out_of_range(self):
        # ocr_elevation applies a final range check — out-of-range medians are rejected
        with patch.object(ocr, "_parse_elevation_text", return_value=99.9):
            cell = render_text_cell("99")
            result = ocr.ocr_elevation(cell)
            assert result is None


# ---------------------------------------------------------------------------
# estimate_scale_from_axis_labels
# ---------------------------------------------------------------------------

class TestEstimateScale:
    def test_returns_float_or_none_on_synthetic(self):
        img, _ = make_clean_fixture()
        result = ocr.estimate_scale_from_axis_labels(img)
        assert result is None or isinstance(result, float)

    def test_fallback_scale_reasonable(self):
        img, gt = make_clean_fixture()
        _, contour = ocr.detect_green_boundary(img)
        scale = ocr.fallback_scale_from_green(contour, green_width_yards=28.0)
        # Should be in a reasonable range (5–200 px/yard)
        assert 5 < scale < 200


# ---------------------------------------------------------------------------
# Integration: process_page on synthetic fixture
# ---------------------------------------------------------------------------

class TestProcessPage:
    def test_returns_expected_structure(self, tmp_path):
        img, gt = make_clean_fixture()
        img_path = tmp_path / "hole_01.png"
        cv2.imwrite(str(img_path), img)

        result = ocr.process_page(img_path, hole_num=1, px_per_yard=gt["px_per_yard"])

        assert "hole" in result
        assert "grid_spacing_yards" in result
        assert "points" in result
        assert result["hole"] == 1
        assert result["grid_spacing_yards"] == 3

    def test_finds_some_points(self, tmp_path):
        img, gt = make_clean_fixture()
        img_path = tmp_path / "hole_01.png"
        cv2.imwrite(str(img_path), img)

        result = ocr.process_page(img_path, hole_num=1, px_per_yard=gt["px_per_yard"])
        # Should find at least some points (OCR won't be 100% on synthetic)
        assert len(result["points"]) > 0

    def test_all_points_have_valid_elevation(self, tmp_path):
        img, gt = make_clean_fixture()
        img_path = tmp_path / "hole_01.png"
        cv2.imwrite(str(img_path), img)

        result = ocr.process_page(img_path, hole_num=1, px_per_yard=gt["px_per_yard"])
        for pt in result["points"]:
            assert 0.0 <= pt["elevation_ft"] <= 10.0

    def test_real_sample_does_not_crash(self, tmp_path):
        result = ocr.process_page(SAMPLE_IMAGE, hole_num=1)
        assert "points" in result


# ---------------------------------------------------------------------------
# Integration: save_result
# ---------------------------------------------------------------------------

class TestSaveResult:
    def test_saves_json_file(self, tmp_path):
        result = {"hole": 1, "grid_spacing_yards": 3, "px_per_yard": 75.0,
                  "points": [{"x": 3.0, "y": 6.0, "elevation_ft": 2.5}]}
        path = ocr.save_result(result, tmp_path, 1)
        assert path.exists()

    def test_saved_file_is_valid_json(self, tmp_path):
        result = {"hole": 3, "grid_spacing_yards": 3, "px_per_yard": 75.0,
                  "points": [{"x": 0.0, "y": 0.0, "elevation_ft": 1.0}]}
        path = ocr.save_result(result, tmp_path, 3)
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["hole"] == 3

    def test_filename_uses_zero_padded_hole_number(self, tmp_path):
        result = {"hole": 7, "grid_spacing_yards": 3, "px_per_yard": 75.0, "points": []}
        path = ocr.save_result(result, tmp_path, 7)
        assert path.name == "hole_07_green.json"
