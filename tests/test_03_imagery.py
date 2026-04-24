"""
Tests for pipeline/03_imagery.py

Unit tests use mocked HTTP responses and synthetic GeoTIFF fixtures.
No live network calls are made.

Run with:
    .venv/bin/pytest tests/test_03_imagery.py -v
"""

import importlib
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
imagery = importlib.import_module("03_imagery")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_synthetic_geotiff(path: Path, width: int = 64, height: int = 64,
                            n_bands: int = 4, crs_epsg: int = 26919):
    """Creates a small synthetic GeoTIFF in UTM Zone 19N for testing."""
    import rasterio
    from rasterio.transform import from_bounds

    # Approximate UTM bounds for Marblehead, MA area (west, south, east, north)
    transform = from_bounds(358000, 4694000, 360000, 4696000, width, height)
    data = np.random.randint(50, 200, (n_bands, height, width), dtype=np.uint8)

    with rasterio.open(
        path, "w",
        driver="GTiff",
        height=height, width=width,
        count=n_bands,
        dtype="uint8",
        crs=f"EPSG:{crs_epsg}",
        transform=transform,
    ) as ds:
        ds.write(data)

    return path


def make_stac_feature(year: str = "2023", tile_id: str = "ma_test_tile") -> dict:
    """Builds a minimal STAC feature dict mimicking Planetary Computer NAIP response."""
    return {
        "id": tile_id,
        "type": "Feature",
        "properties": {
            "datetime": f"{year}-07-05T16:00:00Z",
            "naip:year": year,
            "naip:state": "ma",
        },
        "assets": {
            "image": {
                "href": f"https://naipeuwest.blob.core.windows.net/naip/v002/ma/{year}/ma_030cm_{year}/42070/{tile_id}.tif",
                "type": "image/tiff",
            },
            "thumbnail": {
                "href": f"https://example.com/{tile_id}_thumb.jpg",
            },
        },
    }


# ---------------------------------------------------------------------------
# bbox_from_center
# ---------------------------------------------------------------------------

class TestBboxFromCenter:
    def test_output_keys(self):
        bbox = imagery.bbox_from_center(42.4997, -70.8639, 1.5)
        assert set(bbox.keys()) == {"xmin", "ymin", "xmax", "ymax"}

    def test_center_inside_bbox(self):
        bbox = imagery.bbox_from_center(42.4997, -70.8639, 1.5)
        assert bbox["xmin"] < -70.8639 < bbox["xmax"]
        assert bbox["ymin"] < 42.4997 < bbox["ymax"]

    def test_zero_buffer_collapses_to_point(self):
        bbox = imagery.bbox_from_center(42.4997, -70.8639, 0.0)
        assert math.isclose(bbox["xmin"], bbox["xmax"], abs_tol=1e-10)
        assert math.isclose(bbox["ymin"], bbox["ymax"], abs_tol=1e-10)

    def test_larger_buffer_gives_larger_bbox(self):
        small = imagery.bbox_from_center(42.4997, -70.8639, 1.0)
        large = imagery.bbox_from_center(42.4997, -70.8639, 3.0)
        assert large["xmax"] - large["xmin"] > small["xmax"] - small["xmin"]

    def test_approximate_latitude_span(self):
        bbox = imagery.bbox_from_center(42.4997, -70.8639, 1.5)
        lat_span = bbox["ymax"] - bbox["ymin"]
        assert 0.020 < lat_span < 0.035


# ---------------------------------------------------------------------------
# bbox_to_string / bbox_to_list
# ---------------------------------------------------------------------------

class TestBboxHelpers:
    def test_to_string_format(self):
        bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        assert imagery.bbox_to_string(bbox) == "-71.0,42.0,-70.0,43.0"

    def test_to_list_order(self):
        bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        result = imagery.bbox_to_list(bbox)
        assert result == [-71.0, 42.0, -70.0, 43.0]

    def test_to_list_length(self):
        bbox = imagery.bbox_from_center(42.5, -70.9, 1.5)
        assert len(imagery.bbox_to_list(bbox)) == 4


# ---------------------------------------------------------------------------
# query_naip (mocked)
# ---------------------------------------------------------------------------

MOCK_STAC_RESPONSE = {
    "type": "FeatureCollection",
    "features": [
        make_stac_feature("2023", "ma_tile_001"),
        make_stac_feature("2023", "ma_tile_002"),
        make_stac_feature("2021", "ma_tile_003"),
    ]
}


class TestQueryNaip:
    @patch("requests.post")
    def test_returns_features(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_STAC_RESPONSE
        mock_post.return_value = mock_resp

        bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        items = imagery.query_naip(bbox)
        assert len(items) == 3

    @patch("requests.post")
    def test_queries_naip_collection(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"features": []}
        mock_post.return_value = mock_resp

        bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        imagery.query_naip(bbox)

        payload = mock_post.call_args[1]["json"]
        assert "naip" in payload["collections"]

    @patch("requests.post")
    def test_bbox_in_payload(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"features": []}
        mock_post.return_value = mock_resp

        bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        imagery.query_naip(bbox)

        payload = mock_post.call_args[1]["json"]
        assert payload["bbox"] == [-71.0, 42.0, -70.0, 43.0]

    @patch("requests.post")
    def test_empty_response(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"features": []}
        mock_post.return_value = mock_resp

        bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        assert imagery.query_naip(bbox) == []


# ---------------------------------------------------------------------------
# select_best_naip_tiles
# ---------------------------------------------------------------------------

class TestSelectBestNaipTiles:
    def test_empty_input(self):
        assert imagery.select_best_naip_tiles([]) == []

    def test_selects_most_recent_year(self):
        items = MOCK_STAC_RESPONSE["features"]
        result = imagery.select_best_naip_tiles(items)
        for item in result:
            assert item["properties"]["naip:year"] == "2023"

    def test_returns_all_tiles_from_best_year(self):
        items = MOCK_STAC_RESPONSE["features"]
        result = imagery.select_best_naip_tiles(items)
        assert len(result) == 2

    def test_excludes_older_tiles(self):
        items = MOCK_STAC_RESPONSE["features"]
        result = imagery.select_best_naip_tiles(items)
        ids = [i["id"] for i in result]
        assert "ma_tile_003" not in ids

    def test_single_tile(self):
        items = [make_stac_feature("2022")]
        assert len(imagery.select_best_naip_tiles(items)) == 1

    def test_all_same_year(self):
        items = [make_stac_feature("2021", "a"), make_stac_feature("2021", "b")]
        assert len(imagery.select_best_naip_tiles(items)) == 2

    def test_handles_missing_year_property(self):
        items = [{"id": "x", "properties": {}, "assets": {}}]
        result = imagery.select_best_naip_tiles(items)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# sign_url
# ---------------------------------------------------------------------------

class TestSignUrl:
    @patch("requests.get")
    def test_returns_signed_href(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"href": "https://naipeuwest.blob.core.windows.net/naip/tile.tif?se=2025"}
        mock_get.return_value = mock_resp

        signed = imagery.sign_url("https://naipeuwest.blob.core.windows.net/naip/tile.tif")
        assert signed.startswith("https://")
        assert "se=" in signed

    @patch("requests.get")
    def test_passes_href_to_signing_endpoint(self, mock_get):
        original = "https://naipeuwest.blob.core.windows.net/naip/tile.tif"
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"href": original + "?se=2025"}
        mock_get.return_value = mock_resp

        imagery.sign_url(original)
        call_url = mock_get.call_args[0][0]
        assert original in call_url


# ---------------------------------------------------------------------------
# download_tile
# ---------------------------------------------------------------------------

class TestDownloadTile:
    def test_skips_if_file_exists(self, tmp_path):
        dest = tmp_path / "tile.tif"
        dest.write_bytes(b"existing content")

        with patch("requests.get") as mock_get:
            imagery.download_tile("https://example.com/tile.tif", dest)
            mock_get.assert_not_called()

    def test_returns_dest_path(self, tmp_path):
        dest = tmp_path / "tile.tif"
        dest.write_bytes(b"data")
        result = imagery.download_tile("https://example.com/tile.tif", dest)
        assert result == dest

    @patch("requests.get")
    def test_downloads_to_dest(self, mock_get, tmp_path):
        dest = tmp_path / "new_tile.tif"
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.headers = {"content-length": "13"}
        mock_resp.iter_content.return_value = [b"fake tif data"]
        mock_get.return_value = mock_resp

        imagery.download_tile("https://example.com/tile.tif", dest)
        assert dest.read_bytes() == b"fake tif data"


# ---------------------------------------------------------------------------
# mosaic_and_crop
# ---------------------------------------------------------------------------

class TestMosaicAndCrop:
    def test_creates_output_file(self, tmp_path):
        tile_path = tmp_path / "tile.tif"
        make_synthetic_geotiff(tile_path)

        out = tmp_path / "mosaic.tif"
        bbox = {"xmin": -70.90, "ymin": 42.48, "xmax": -70.85, "ymax": 42.52}
        imagery.mosaic_and_crop([tile_path], bbox, out)
        assert out.exists()

    def test_output_is_valid_geotiff(self, tmp_path):
        import rasterio
        tile_path = tmp_path / "tile.tif"
        make_synthetic_geotiff(tile_path)

        out = tmp_path / "mosaic.tif"
        bbox = {"xmin": -70.90, "ymin": 42.48, "xmax": -70.85, "ymax": 42.52}
        imagery.mosaic_and_crop([tile_path], bbox, out)

        with rasterio.open(out) as ds:
            assert ds.count >= 1
            assert ds.width > 0
            assert ds.height > 0

    def test_multiple_tiles_merged(self, tmp_path):
        import rasterio
        from rasterio.transform import from_bounds

        def make_tile(path, west, east):
            transform = from_bounds(west, 4694000, east, 4696000, 32, 64)
            data = np.ones((4, 64, 32), dtype=np.uint8) * 128
            with rasterio.open(path, "w", driver="GTiff", height=64, width=32,
                               count=4, dtype="uint8", crs="EPSG:26919",
                               transform=transform) as ds:
                ds.write(data)

        tile1 = tmp_path / "tile1.tif"
        tile2 = tmp_path / "tile2.tif"
        make_tile(tile1, 358000, 359000)
        make_tile(tile2, 359000, 360000)

        out = tmp_path / "mosaic.tif"
        bbox = {"xmin": -70.90, "ymin": 42.48, "xmax": -70.85, "ymax": 42.52}
        imagery.mosaic_and_crop([tile1, tile2], bbox, out)
        assert out.exists()


# ---------------------------------------------------------------------------
# export_png
# ---------------------------------------------------------------------------

class TestExportPng:
    def test_creates_png_file(self, tmp_path):
        tif = tmp_path / "imagery.tif"
        make_synthetic_geotiff(tif, width=100, height=80)
        png = tmp_path / "imagery.png"
        imagery.export_png(tif, png)
        assert png.exists()

    def test_output_is_rgb(self, tmp_path):
        from PIL import Image
        tif = tmp_path / "imagery.tif"
        make_synthetic_geotiff(tif, width=100, height=80)
        png = tmp_path / "imagery.png"
        imagery.export_png(tif, png)
        img = Image.open(png)
        assert img.mode == "RGB"
        assert img.width == 100
        assert img.height == 80

    def test_downscales_large_image(self, tmp_path):
        from PIL import Image
        tif = tmp_path / "big.tif"
        make_synthetic_geotiff(tif, width=200, height=200)
        png = tmp_path / "big.png"
        imagery.export_png(tif, png, max_pixels=100)
        img = Image.open(png)
        assert max(img.width, img.height) <= 100

    def test_no_downscale_when_under_limit(self, tmp_path):
        from PIL import Image
        tif = tmp_path / "small.tif"
        make_synthetic_geotiff(tif, width=50, height=50)
        png = tmp_path / "small.png"
        imagery.export_png(tif, png, max_pixels=8192)
        img = Image.open(png)
        assert img.width == 50
        assert img.height == 50

    def test_single_band_produces_rgb(self, tmp_path):
        import rasterio
        from rasterio.transform import from_bounds
        from PIL import Image

        tif = tmp_path / "mono.tif"
        transform = from_bounds(358000, 4694000, 360000, 4696000, 50, 50)
        data = np.ones((1, 50, 50), dtype=np.uint8) * 128
        with rasterio.open(tif, "w", driver="GTiff", height=50, width=50,
                           count=1, dtype="uint8", crs="EPSG:26919",
                           transform=transform) as ds:
            ds.write(data)

        png = tmp_path / "mono.png"
        imagery.export_png(tif, png)
        assert Image.open(png).mode == "RGB"

    def test_creates_parent_directories(self, tmp_path):
        tif = tmp_path / "src.tif"
        make_synthetic_geotiff(tif)
        png = tmp_path / "deep" / "nested" / "out.png"
        imagery.export_png(tif, png)
        assert png.exists()

    def test_returns_path(self, tmp_path):
        tif = tmp_path / "img.tif"
        make_synthetic_geotiff(tif)
        png = tmp_path / "img.png"
        assert imagery.export_png(tif, png) == png
