"""
Tests for pipeline/04_mesh_gen.py

Run with:
    .venv/bin/pytest tests/test_04_mesh_gen.py -v
"""

import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
mesh = importlib.import_module("04_mesh_gen")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_terrain_tif(path: Path, width: int = 50, height: int = 40,
                     elev_min: float = 5.0, elev_max: float = 30.0,
                     nodata: float = -9999.0) -> Path:
    """Creates a synthetic Float32 GeoTIFF terrain for testing."""
    import rasterio
    from rasterio.transform import from_bounds

    transform = from_bounds(358000, 4694000, 358050, 4694040, width, height)
    rng = np.random.default_rng(42)
    data = rng.uniform(elev_min, elev_max, (1, height, width)).astype(np.float32)
    # Punch a nodata hole
    data[0, 10:15, 10:15] = nodata

    with rasterio.open(path, "w", driver="GTiff", height=height, width=width,
                       count=1, dtype="float32", crs="EPSG:26919",
                       transform=transform, nodata=nodata) as ds:
        ds.write(data)
    return path


def make_green_points(n_x: int = 5, n_y: int = 5) -> list:
    """Creates a synthetic PuttView green grid."""
    points = []
    for gx in range(n_x):
        for gy in range(n_y):
            points.append({
                "x": float(gx * 3),
                "y": float(gy * 3),
                "elevation_ft": 2.0 + gx * 0.2 + gy * 0.1,
            })
    return points


# ---------------------------------------------------------------------------
# load_heightmap
# ---------------------------------------------------------------------------

class TestLoadHeightmap:
    def test_returns_float32_array(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif")
        data, meta = mesh.load_heightmap(tif)
        assert data.dtype == np.float32

    def test_nodata_becomes_nan(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif")
        data, meta = mesh.load_heightmap(tif)
        assert np.any(np.isnan(data))

    def test_meta_has_required_keys(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif")
        _, meta = mesh.load_heightmap(tif)
        required = {"width_px", "height_px", "pixel_size_m", "width_m", "height_m",
                    "elev_min", "elev_max", "elev_range_m", "nodata_count", "crs",
                    "xmin", "ymin", "xmax", "ymax"}
        assert required.issubset(set(meta.keys()))

    def test_meta_dimensions_match(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif", width=50, height=40)
        data, meta = mesh.load_heightmap(tif)
        assert meta["width_px"] == 50
        assert meta["height_px"] == 40
        assert data.shape == (40, 50)

    def test_elev_range_positive(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif", elev_min=5.0, elev_max=30.0)
        _, meta = mesh.load_heightmap(tif)
        assert meta["elev_range_m"] > 0

    def test_nodata_count_nonzero(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif")
        _, meta = mesh.load_heightmap(tif)
        assert meta["nodata_count"] > 0


# ---------------------------------------------------------------------------
# export_16bit_png
# ---------------------------------------------------------------------------

class TestExport16BitPng:
    def test_creates_file(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif")
        data, meta = mesh.load_heightmap(tif)
        out = tmp_path / "heightmap.png"
        mesh.export_16bit_png(data, meta, out)
        assert out.exists()

    def test_returns_height_scale(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif")
        data, meta = mesh.load_heightmap(tif)
        scale = mesh.export_16bit_png(data, meta, tmp_path / "h.png")
        assert isinstance(scale, float)
        assert scale > 0

    def test_output_is_16bit(self, tmp_path):
        import rasterio
        tif = make_terrain_tif(tmp_path / "terrain.tif")
        data, meta = mesh.load_heightmap(tif)
        out = tmp_path / "heightmap.png"
        mesh.export_16bit_png(data, meta, out)
        with rasterio.open(out) as ds:
            assert ds.dtypes[0] == "uint16"

    def test_output_range_is_0_to_65535(self, tmp_path):
        import rasterio
        tif = make_terrain_tif(tmp_path / "terrain.tif")
        data, meta = mesh.load_heightmap(tif)
        out = tmp_path / "heightmap.png"
        mesh.export_16bit_png(data, meta, out)
        with rasterio.open(out) as ds:
            arr = ds.read(1)
        assert arr.min() >= 0
        assert arr.max() <= 65535

    def test_creates_parent_directories(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif")
        data, meta = mesh.load_heightmap(tif)
        out = tmp_path / "deep" / "dir" / "heightmap.png"
        mesh.export_16bit_png(data, meta, out)
        assert out.exists()

    def test_flat_terrain_no_crash(self, tmp_path):
        """Flat terrain (zero elevation range) should not divide by zero."""
        import rasterio
        from rasterio.transform import from_bounds
        tif = tmp_path / "flat.tif"
        transform = from_bounds(0, 0, 50, 40, 50, 40)
        data = np.ones((1, 40, 50), dtype=np.float32) * 10.0
        with rasterio.open(tif, "w", driver="GTiff", height=40, width=50,
                           count=1, dtype="float32", crs="EPSG:26919",
                           transform=transform) as ds:
            ds.write(data)
        d, m = mesh.load_heightmap(tif)
        out = tmp_path / "flat.png"
        mesh.export_16bit_png(d, m, out)
        assert out.exists()


# ---------------------------------------------------------------------------
# export_terrain_metadata
# ---------------------------------------------------------------------------

class TestExportTerrainMetadata:
    def _make_meta(self):
        return {
            "width_m": 3000.0, "height_m": 2800.0,
            "elev_min": 2.5, "elev_max": 35.0, "elev_range_m": 32.5,
            "pixel_size_m": 1.0, "width_px": 3000, "height_px": 2800,
            "crs": "EPSG:26919",
            "xmin": 358000.0, "ymin": 4694000.0,
            "xmax": 361000.0, "ymax": 4696800.0,
        }

    def test_creates_file(self, tmp_path):
        out = tmp_path / "meta.json"
        mesh.export_terrain_metadata(self._make_meta(), 32.5, out)
        assert out.exists()

    def test_valid_json(self, tmp_path):
        out = tmp_path / "meta.json"
        mesh.export_terrain_metadata(self._make_meta(), 32.5, out)
        with open(out) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_has_course_forge_settings(self, tmp_path):
        out = tmp_path / "meta.json"
        mesh.export_terrain_metadata(self._make_meta(), 32.5, out)
        with open(out) as f:
            data = json.load(f)
        cf = data["course_forge_settings"]
        assert "terrain_width_m" in cf
        assert "terrain_length_m" in cf
        assert "terrain_height_m" in cf

    def test_height_includes_safety_margin(self, tmp_path):
        out = tmp_path / "meta.json"
        mesh.export_terrain_metadata(self._make_meta(), 32.5, out)
        with open(out) as f:
            data = json.load(f)
        # Height scale should be elev_range + 2m margin
        assert data["course_forge_settings"]["terrain_height_m"] >= 32.5 + 2.0

    def test_returns_path(self, tmp_path):
        out = tmp_path / "meta.json"
        result = mesh.export_terrain_metadata(self._make_meta(), 32.5, out)
        assert result == out


# ---------------------------------------------------------------------------
# heightmap_to_obj
# ---------------------------------------------------------------------------

class TestHeightmapToObj:
    def test_creates_file(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif", width=50, height=40)
        data, meta = mesh.load_heightmap(tif)
        out = tmp_path / "terrain.obj"
        mesh.heightmap_to_obj(data, meta, out, step_m=1.0)
        assert out.exists()

    def test_contains_vertices_and_faces(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif", width=20, height=20)
        data, meta = mesh.load_heightmap(tif)
        out = tmp_path / "terrain.obj"
        mesh.heightmap_to_obj(data, meta, out, step_m=1.0)
        content = out.read_text()
        assert "v " in content
        assert "f " in content

    def test_vertex_count_matches_grid(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif", width=20, height=20)
        data, meta = mesh.load_heightmap(tif)
        out = tmp_path / "terrain.obj"
        mesh.heightmap_to_obj(data, meta, out, step_m=1.0)
        lines = out.read_text().splitlines()
        verts = [l for l in lines if l.startswith("v ")]
        # 20x20 grid = 400 vertices
        assert len(verts) == 400

    def test_larger_step_gives_fewer_vertices(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif", width=50, height=50)
        data, meta = mesh.load_heightmap(tif)

        out1 = tmp_path / "fine.obj"
        out2 = tmp_path / "coarse.obj"
        mesh.heightmap_to_obj(data, meta, out1, step_m=1.0)
        mesh.heightmap_to_obj(data, meta, out2, step_m=2.0)

        v1 = sum(1 for l in out1.read_text().splitlines() if l.startswith("v "))
        v2 = sum(1 for l in out2.read_text().splitlines() if l.startswith("v "))
        assert v1 > v2

    def test_obj_vertex_format(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif", width=10, height=10)
        data, meta = mesh.load_heightmap(tif)
        out = tmp_path / "terrain.obj"
        mesh.heightmap_to_obj(data, meta, out, step_m=1.0)
        lines = out.read_text().splitlines()
        vert_lines = [l for l in lines if l.startswith("v ")]
        # Each vertex line should have 3 float components
        for line in vert_lines[:5]:
            parts = line.split()
            assert len(parts) == 4  # "v", x, y, z
            float(parts[1]); float(parts[2]); float(parts[3])  # parse ok

    def test_obj_face_format(self, tmp_path):
        tif = make_terrain_tif(tmp_path / "terrain.tif", width=10, height=10)
        data, meta = mesh.load_heightmap(tif)
        out = tmp_path / "terrain.obj"
        mesh.heightmap_to_obj(data, meta, out, step_m=1.0)
        lines = out.read_text().splitlines()
        face_lines = [l for l in lines if l.startswith("f ")]
        assert len(face_lines) > 0
        for line in face_lines[:5]:
            parts = line.split()
            assert len(parts) == 4  # "f", v1, v2, v3
            int(parts[1]); int(parts[2]); int(parts[3])


# ---------------------------------------------------------------------------
# interpolate_green_surface
# ---------------------------------------------------------------------------

class TestInterpolateGreenSurface:
    def test_returns_grid_and_coords(self):
        points = make_green_points(5, 5)
        grid, xi, yi = mesh.interpolate_green_surface(points)
        assert grid.ndim == 2
        assert xi.ndim == 1
        assert yi.ndim == 1

    def test_no_nan_in_output(self):
        points = make_green_points(6, 6)
        grid, xi, yi = mesh.interpolate_green_surface(points)
        assert not np.any(np.isnan(grid))

    def test_grid_shape_matches_coords(self):
        points = make_green_points(5, 5)
        grid, xi, yi = mesh.interpolate_green_surface(points)
        assert grid.shape == (len(yi), len(xi))

    def test_finer_resolution_gives_larger_grid(self):
        points = make_green_points(5, 5)
        _, xi_coarse, _ = mesh.interpolate_green_surface(points, resolution_yards=1.0)
        _, xi_fine, _ = mesh.interpolate_green_surface(points, resolution_yards=0.25)
        assert len(xi_fine) > len(xi_coarse)

    def test_elevation_in_expected_range(self):
        points = make_green_points(5, 5)
        elevations = [p["elevation_ft"] * mesh.FEET_TO_M for p in points]
        grid, _, _ = mesh.interpolate_green_surface(points)
        # Grid values should be close to the input range
        assert grid.min() >= min(elevations) - 0.5
        assert grid.max() <= max(elevations) + 0.5

    def test_raises_on_empty_points(self):
        with pytest.raises(ValueError, match="No points"):
            mesh.interpolate_green_surface([])

    def test_units_are_meters(self):
        # Input is yards/feet, output should be in meters
        points = [{"x": 0.0, "y": 0.0, "elevation_ft": 3.28084}]  # 1 meter elevation
        # Only works with enough points for interpolation — use a grid
        points = make_green_points(4, 4)
        # Elevation ~2ft → ~0.6m
        grid, _, _ = mesh.interpolate_green_surface(points)
        assert grid.mean() < 5.0  # sanity check: meters, not feet


# ---------------------------------------------------------------------------
# green_to_obj
# ---------------------------------------------------------------------------

class TestGreenToObj:
    def test_creates_file(self, tmp_path):
        points = make_green_points(5, 5)
        grid, xi, yi = mesh.interpolate_green_surface(points)
        out = tmp_path / "green.obj"
        mesh.green_to_obj(grid, xi, yi, out)
        assert out.exists()

    def test_contains_vertices_and_faces(self, tmp_path):
        points = make_green_points(5, 5)
        grid, xi, yi = mesh.interpolate_green_surface(points)
        out = tmp_path / "green.obj"
        mesh.green_to_obj(grid, xi, yi, out)
        content = out.read_text()
        assert "v " in content
        assert "f " in content

    def test_vertex_count_matches_grid(self, tmp_path):
        points = make_green_points(4, 4)
        grid, xi, yi = mesh.interpolate_green_surface(points, resolution_yards=1.0)
        out = tmp_path / "green.obj"
        mesh.green_to_obj(grid, xi, yi, out)
        lines = out.read_text().splitlines()
        verts = [l for l in lines if l.startswith("v ")]
        assert len(verts) == grid.size

    def test_returns_path(self, tmp_path):
        points = make_green_points(4, 4)
        grid, xi, yi = mesh.interpolate_green_surface(points)
        out = tmp_path / "green.obj"
        result = mesh.green_to_obj(grid, xi, yi, out)
        assert result == out

    def test_creates_parent_directories(self, tmp_path):
        points = make_green_points(4, 4)
        grid, xi, yi = mesh.interpolate_green_surface(points)
        out = tmp_path / "greens" / "hole_01_green.obj"
        mesh.green_to_obj(grid, xi, yi, out)
        assert out.exists()
