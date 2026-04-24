"""
Tests for pipeline/01_lidar.py

Covers:
- Bounding box calculation
- TNM API query (mocked)
- Product selection logic
- PDAL pipeline JSON structure
- Post-processing (void fill + smoothing)

Run with:
    .venv/bin/pytest tests/test_01_lidar.py -v
"""

import math
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Add pipeline to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import importlib
lidar = importlib.import_module("01_lidar")


FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Bounding box
# ---------------------------------------------------------------------------

class TestBboxFromCenter:
    def test_output_keys(self):
        bbox = lidar.bbox_from_center(42.4997, -70.8639, 1.5)
        assert set(bbox.keys()) == {"xmin", "ymin", "xmax", "ymax"}

    def test_bbox_larger_than_center(self):
        bbox = lidar.bbox_from_center(42.4997, -70.8639, 1.5)
        assert bbox["xmin"] < -70.8639 < bbox["xmax"]
        assert bbox["ymin"] < 42.4997 < bbox["ymax"]

    def test_buffer_zero_collapses_to_point(self):
        bbox = lidar.bbox_from_center(42.4997, -70.8639, 0.0)
        assert math.isclose(bbox["xmin"], bbox["xmax"], abs_tol=1e-10)
        assert math.isclose(bbox["ymin"], bbox["ymax"], abs_tol=1e-10)

    def test_larger_buffer_gives_larger_bbox(self):
        small = lidar.bbox_from_center(42.4997, -70.8639, 1.0)
        large = lidar.bbox_from_center(42.4997, -70.8639, 3.0)
        assert large["xmax"] - large["xmin"] > small["xmax"] - small["xmin"]
        assert large["ymax"] - large["ymin"] > small["ymax"] - small["ymin"]

    def test_approximate_size(self):
        # 1.5km buffer → ~3km wide box → ~0.027 degrees lat
        bbox = lidar.bbox_from_center(42.4997, -70.8639, 1.5)
        lat_span = bbox["ymax"] - bbox["ymin"]
        assert 0.020 < lat_span < 0.035


class TestBboxToString:
    def test_format(self):
        bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        result = lidar.bbox_to_string(bbox)
        assert result == "-71.0,42.0,-70.0,43.0"


# ---------------------------------------------------------------------------
# TNM API query (mocked)
# ---------------------------------------------------------------------------

MOCK_TNM_RESPONSE = {
    "items": [
        {
            "title": "MA LiDAR 2021",
            "groundSampleDistance": 1.0,
            "publicationDate": "2021-06-01",
            "sizeInBytes": 500_000_000,
            "downloadURL": "https://example.com/ma_lidar_2021.laz"
        },
        {
            "title": "MA LiDAR 2018",
            "groundSampleDistance": 2.0,
            "publicationDate": "2018-03-15",
            "sizeInBytes": 200_000_000,
            "downloadURL": "https://example.com/ma_lidar_2018.laz"
        }
    ]
}


class TestQueryTNM:
    @patch("requests.get")
    def test_returns_items(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_TNM_RESPONSE
        mock_get.return_value = mock_resp

        bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        items = lidar.query_tnm(bbox)
        assert len(items) == 2

    @patch("requests.get")
    def test_passes_correct_dataset(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_get.return_value = mock_resp

        bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        lidar.query_tnm(bbox)

        call_params = mock_get.call_args[1]["params"]
        assert call_params["datasets"] == lidar.LIDAR_DATASET

    @patch("requests.get")
    def test_empty_response(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_get.return_value = mock_resp

        bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        items = lidar.query_tnm(bbox)
        assert items == []


# ---------------------------------------------------------------------------
# Product selection
# ---------------------------------------------------------------------------

MOCK_TNM_MULTI_DATASET = {
    "items": [
        # Older dataset — two tiles
        {
            "title": "USGS Lidar Point Cloud OLD_SURVEY_2011 tile_001",
            "groundSampleDistance": None,
            "publicationDate": "2014-01-01",
            "sizeInBytes": 50_000_000,
            "downloadURL": "https://example.com/old_001.laz"
        },
        {
            "title": "USGS Lidar Point Cloud OLD_SURVEY_2011 tile_002",
            "groundSampleDistance": None,
            "publicationDate": "2014-01-01",
            "sizeInBytes": 60_000_000,
            "downloadURL": "https://example.com/old_002.laz"
        },
        # Newer dataset — two tiles (should be selected)
        {
            "title": "USGS Lidar Point Cloud NEW_SURVEY_2021 tile_A",
            "groundSampleDistance": None,
            "publicationDate": "2022-10-13",
            "sizeInBytes": 100_000_000,
            "downloadURL": "https://example.com/new_A.laz"
        },
        {
            "title": "USGS Lidar Point Cloud NEW_SURVEY_2021 tile_B",
            "groundSampleDistance": None,
            "publicationDate": "2022-10-13",
            "sizeInBytes": 120_000_000,
            "downloadURL": "https://example.com/new_B.laz"
        },
    ]
}


class TestSelectBestProducts:
    def test_empty_input(self):
        assert lidar.select_best_products([]) == []

    def test_prefers_most_recent_dataset(self):
        items = MOCK_TNM_MULTI_DATASET["items"]
        result = lidar.select_best_products(items)
        # Should only return tiles from the 2022 dataset
        for item in result:
            assert item["publicationDate"] == "2022-10-13"

    def test_returns_all_tiles_from_best_dataset(self):
        items = MOCK_TNM_MULTI_DATASET["items"]
        result = lidar.select_best_products(items)
        # Both 2022 tiles should be returned
        assert len(result) == 2

    def test_excludes_older_dataset_tiles(self):
        items = MOCK_TNM_MULTI_DATASET["items"]
        result = lidar.select_best_products(items)
        urls = [item["downloadURL"] for item in result]
        assert "https://example.com/old_001.laz" not in urls
        assert "https://example.com/old_002.laz" not in urls

    def test_prefers_lower_gsd_over_newer_date(self):
        items = [
            {
                "title": "USGS Lidar Point Cloud HIGH_RES_2018 tile_1",
                "groundSampleDistance": 0.5,
                "publicationDate": "2018-01-01",
                "downloadURL": "https://example.com/hires.laz"
            },
            {
                "title": "USGS Lidar Point Cloud LOW_RES_2022 tile_1",
                "groundSampleDistance": 2.0,
                "publicationDate": "2022-01-01",
                "downloadURL": "https://example.com/lowres.laz"
            },
        ]
        result = lidar.select_best_products(items)
        assert result[0]["groundSampleDistance"] == 0.5

    def test_handles_single_item(self):
        items = [MOCK_TNM_RESPONSE["items"][0]]
        result = lidar.select_best_products(items)
        assert len(result) == 1

    def test_handles_missing_gsd(self):
        items = [{"title": "USGS Lidar Point Cloud X Y tile_1", "downloadURL": "https://example.com/a.laz"}]
        result = lidar.select_best_products(items)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# PDAL pipeline structure
# ---------------------------------------------------------------------------

class TestBboxToUtm:
    def test_returns_four_keys(self):
        bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        result = lidar.bbox_to_utm(bbox, 32619)
        assert set(result.keys()) == {"xmin", "ymin", "xmax", "ymax"}

    def test_values_are_in_meters(self):
        # UTM easting for eastern MA should be ~300,000–400,000m
        bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        result = lidar.bbox_to_utm(bbox, 32619)
        assert 200_000 < result["xmin"] < 700_000
        assert 200_000 < result["xmax"] < 700_000

    def test_xmin_less_than_xmax(self):
        bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        result = lidar.bbox_to_utm(bbox, 32619)
        assert result["xmin"] < result["xmax"]
        assert result["ymin"] < result["ymax"]


class TestBuildPdalPipeline:
    def setup_method(self):
        self.laz_files = [Path("/tmp/test.laz")]
        self.bbox = {"xmin": -71.0, "ymin": 42.0, "xmax": -70.0, "ymax": 43.0}
        self.output = Path("/tmp/output.tif")
        self.pipeline = lidar.build_pdal_pipeline(
            self.laz_files, self.bbox, self.output, 1.0, 32619
        )

    def test_has_pipeline_key(self):
        assert "pipeline" in self.pipeline

    def test_has_reader(self):
        stages = self.pipeline["pipeline"]
        readers = [s for s in stages if s.get("type") == "readers.las"]
        assert len(readers) == len(self.laz_files)

    def test_reader_uses_correct_file(self):
        stages = self.pipeline["pipeline"]
        reader = next(s for s in stages if s.get("type") == "readers.las")
        assert reader["filename"] == str(self.laz_files[0])

    def test_reprojection_comes_before_crop(self):
        stages = self.pipeline["pipeline"]
        types = [s.get("type") for s in stages]
        assert types.index("filters.reprojection") < types.index("filters.crop")

    def test_has_crop_filter(self):
        stages = self.pipeline["pipeline"]
        assert any(s.get("type") == "filters.crop" for s in stages)

    def test_crop_uses_utm_coordinates(self):
        # UTM eastings for eastern MA are ~300,000–500,000, not negative like WGS84 lon
        stages = self.pipeline["pipeline"]
        crop = next(s for s in stages if s.get("type") == "filters.crop")
        assert "-71" not in crop["bounds"]  # WGS84 lon should not appear

    def test_has_reprojection(self):
        stages = self.pipeline["pipeline"]
        reproj = next(s for s in stages if s.get("type") == "filters.reprojection")
        assert reproj["out_srs"] == "EPSG:32619"
        assert "in_srs" not in reproj  # CRS must come from file header

    def test_has_smrf_filter(self):
        stages = self.pipeline["pipeline"]
        assert any(s.get("type") == "filters.smrf" for s in stages)

    def test_has_range_filter_for_ground(self):
        stages = self.pipeline["pipeline"]
        range_filter = next(s for s in stages if s.get("type") == "filters.range")
        assert "Classification[2:2]" in range_filter["limits"]

    def test_has_gdal_writer(self):
        stages = self.pipeline["pipeline"]
        writer = next(s for s in stages if s.get("type") == "writers.gdal")
        assert writer["filename"] == str(self.output)
        assert writer["resolution"] == 1.0

    def test_multiple_laz_files(self):
        files = [Path("/tmp/a.laz"), Path("/tmp/b.laz"), Path("/tmp/c.laz")]
        pipeline = lidar.build_pdal_pipeline(files, self.bbox, self.output, 1.0, 32619)
        readers = [s for s in pipeline["pipeline"] if s.get("type") == "readers.las"]
        assert len(readers) == 3



# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

class TestPostprocessHeightmap:
    def test_fills_nodata_voids(self, tmp_path):
        import rasterio
        from rasterio.transform import from_bounds

        tif = tmp_path / "terrain.tif"
        data = np.ones((50, 50), dtype=np.float32) * 10.0
        nodata = -9999.0

        # Punch a hole in the middle (simulates tree canopy void)
        data[20:30, 20:30] = nodata

        transform = from_bounds(0, 0, 1, 1, 50, 50)
        with rasterio.open(
            tif, "w", driver="GTiff", height=50, width=50,
            count=1, dtype="float32", crs="EPSG:32619",
            transform=transform, nodata=nodata
        ) as ds:
            ds.write(data, 1)

        lidar.postprocess_heightmap(tif)

        with rasterio.open(tif) as ds:
            result = ds.read(1)

        # Void region should now have real values
        void_region = result[20:30, 20:30]
        assert not np.any(void_region == nodata)

    def test_handles_missing_imports_gracefully(self, tmp_path, monkeypatch):
        # If rasterio isn't available, should warn and return without crashing
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "rasterio":
                raise ImportError("mocked missing")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        tif = tmp_path / "terrain.tif"
        tif.touch()
        # Should not raise
        lidar.postprocess_heightmap(tif)
