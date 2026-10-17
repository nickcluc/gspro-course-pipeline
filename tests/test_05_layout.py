"""
Tests for pipeline/00_arccos.py and pipeline/05_layout.py

Run with:
    .venv/bin/pytest tests/test_05_layout.py -v

Uses a synthetic two-hole course near Tedesco; no network or Arccos login.
"""

import importlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import utils  # noqa: E402

layout = importlib.import_module("05_layout")
arccos = importlib.import_module("00_arccos")

LAT0, LON0 = 42.4837, -70.8949
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))


def ll(east_m: float, north_m: float) -> dict:
    """lat/lon of a point east_m, north_m from the test origin."""
    return {"latitude": LAT0 + north_m / M_PER_DEG_LAT, "longitude": LON0 + east_m / M_PER_DEG_LON}


def square(cx: float, cy: float, half: float) -> list[dict]:
    return [ll(cx - half, cy - half), ll(cx + half, cy - half), ll(cx + half, cy + half), ll(cx - half, cy + half)]


def make_hole(n: int, east: float) -> dict:
    """Green at (east, 0); tee boxes 350 m and 330 m south of it."""
    return {
        "holeId": n, "mensPar": 4, "mensHandicap": 0,
        "holeTees": [{"teeId": 1, "name": "Black", "distance": 350.0},
                     {"teeId": 2, "name": "White", "distance": 330.0}],
        "features": [
            {"featureId": 1, "coordinates": square(east, 0, 12)},
            {"featureId": 3, "coordinates": square(east + 20, 0, 4)},
            {"featureId": 7, "coordinates": square(east, -150, 20)},
            {"featureId": 8, "coordinates": square(east, -150, 3)},
            {"featureId": 6, "coordinates": square(east, -350, 5)},
            {"featureId": 6, "coordinates": square(east, -330, 5)},
            {"featureId": 9, "coordinates": [ll(east + 60, -100), ll(east + 70, -100)]},  # too few points
        ],
        "elevations": [dict(ll(east, -200), elevation=19.8), dict(ll(east, -100), elevation=19.6)],
    }


@pytest.fixture
def course(tmp_path, monkeypatch):
    """A fake repo with one course 'test' and two Arccos hole maps."""
    monkeypatch.setattr(utils, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(layout, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(arccos, "REPO_ROOT", tmp_path)
    base = tmp_path / "courses" / "test"
    base.mkdir(parents=True)
    (base / "course.json").write_text(json.dumps({
        "name": "Test Course", "slug": "test", "holes": 2,
        "lidar": {"epsg_output": 32619}, "arccos": {"course_id": 1, "course_version": 4}}))
    holes_dir = base / "data" / "raw" / "arccos" / "holes"
    holes_dir.mkdir(parents=True)
    for n, east in ((1, 0.0), (2, 200.0)):
        (holes_dir / f"hole_{n:02d}.json").write_text(json.dumps(make_hole(n, east)))
    return base


def make_terrain(base: Path, elev: float = 20.0) -> Path:
    import rasterio
    from rasterio.transform import from_bounds
    project = layout.make_projector(32619)
    e0, n0 = project(LON0, LAT0)
    xmin, ymin, xmax, ymax = e0 - 100, n0 - 400, e0 + 300, n0 + 100
    path = base / "data" / "processed" / "heightmaps" / "test_terrain.tif"
    path.parent.mkdir(parents=True)
    with rasterio.open(path, "w", driver="GTiff", height=50, width=40, count=1, dtype="float32",
                       crs="EPSG:32619", transform=from_bounds(xmin, ymin, xmax, ymax, 40, 50)) as ds:
        ds.write(np.full((1, 50, 40), elev, dtype=np.float32))
    return path


# ---------------------------------------------------------------------------
# Stage 00: download
# ---------------------------------------------------------------------------

class TestArccosDownload:
    def test_fetches_each_hole_once(self, course):
        calls = []
        get = lambda p: calls.append(p) or {"holeId": 1}  # noqa: E731
        out = course / "data" / "raw" / "arccos" / "fresh"
        arccos.fetch_holes(1, 4, 3, out, get)
        arccos.fetch_holes(1, 4, 3, out, get)  # second run is cached
        assert calls == [f"/courses/1/holes/{n}?courseVersion=4" for n in (1, 2, 3)]

    def test_rounds_filtered_to_course(self, course):
        page = {"totalCount": 3, "rounds": [
            {"roundId": 10, "courseId": 1}, {"roundId": 11, "courseId": 2},
            {"roundId": 12, "courseId": 1, "shouldIgnore": "T"}]}
        get = lambda p: page if "/v2/" in p else {"roundId": int(p.rsplit("/", 1)[1])}  # noqa: E731
        paths = arccos.fetch_rounds("u1", 1, course / "r", get)
        assert [p.stem for p in paths] == ["10"]

    def test_auth_from_env_is_url_decoded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ARCCOS_ACCESS_TOKEN", "abc%3D")
        monkeypatch.setenv("ARCCOS_USER_ID", "u1")
        assert arccos.load_auth(tmp_path / "none.json") == ("abc=", "u1")

    def test_auth_from_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ARCCOS_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("ARCCOS_USER_ID", raising=False)
        f = tmp_path / "auth.json"
        f.write_text(json.dumps({"access_token": "t", "user_id": "u"}))
        assert arccos.load_auth(f) == ("t", "u")

    def test_missing_arccos_config_exits(self, course):
        cfg = json.loads((course / "course.json").read_text())
        del cfg["arccos"]
        (course / "course.json").write_text(json.dumps(cfg))
        with pytest.raises(SystemExit):
            arccos.run("test", get=lambda p: {})


# ---------------------------------------------------------------------------
# Stage 05: layout
# ---------------------------------------------------------------------------

class TestGeometry:
    def test_centroid_of_square(self):
        assert layout.centroid([(0, 0), (2, 0), (2, 2), (0, 2)]) == pytest.approx((1, 1))

    def test_local_frame_matches_stage_04(self):
        # Stage 04 puts the terrain centre at the origin, x east, z north
        to_local = layout.local_frame({"xmin": 100, "ymin": 200, "xmax": 300, "ymax": 600})
        assert to_local(200, 400) == (0, 0)
        assert to_local(300, 600) == (100, 200)

    def test_tees_match_boxes_by_yardage(self):
        boxes = [(0, -330), (0, -350)]
        tees = layout.match_tees([{"teeId": 1, "distance": 350.0},
                                  {"teeId": 2, "distance": 330.0}], boxes, (0, 0))
        assert [t["box"] for t in tees] == [1, 0]
        assert all(t["error_m"] < 0.1 and t["placed_by"] == "distance" for t in tees)
        assert tees[0]["yards"] == pytest.approx(382.8, abs=0.1)

    def test_dogleg_distance_goes_via_fairway_centre(self):
        # Straight-line, box 0 is 283 m out; via the corner at (200, 0) it is 400 m
        boxes = [(200, -200), (0, -390)]
        tees = layout.match_tees([{"teeId": 1, "distance": 400.0}], boxes, (0, 0), route_en=(200, 0))
        assert tees[0]["box"] == 0

    def test_observed_tee_shots_beat_distance(self):
        boxes = [(0, -330), (0, -350)]
        seen = {1: {"en": (1, -331), "rounds": 5}}
        tees = layout.match_tees([{"teeId": 1, "distance": 350.0}], boxes, (0, 0), observed=seen)
        assert tees[0]["box"] == 0 and tees[0]["placed_by"].startswith("your tee shots")
        assert tees[0]["en"] == (0, -330)

    def test_tee_far_from_any_box_uses_your_shots(self):
        boxes = [(0, -330)]
        seen = {1: {"en": (0, -380), "rounds": 5}}
        t = layout.match_tees([{"teeId": 1, "distance": 380.0}], boxes, (0, 0), observed=seen)[0]
        assert t["box"] is None and t["en"] == (0, -380)
        assert "missing" in t["note"]

    def test_tee_starts_need_three_rounds(self):
        project = lambda lon, lat: (lon, lat)  # noqa: E731
        r = lambda e: {"courseVersion": 4, "teeId": 1, "holes": [  # noqa: E731
            {"holeId": 1, "shots": [{"startLong": e, "startLat": 0.0}]}]}
        assert layout.tee_starts([r(0), r(2)], 4, project) == {}
        assert layout.tee_starts([r(0), r(2), r(4)], 4, project)[1][1]["en"] == (2, 0.0)

    def test_pins_cluster_and_rank(self):
        project = lambda lon, lat: (lon, lat)  # noqa: E731
        hole = lambda e: {"holeId": 1, "pinLong": e, "pinLat": 0.0}  # noqa: E731
        rounds = [{"courseVersion": 4, "holes": [hole(0.4)]}, {"courseVersion": 4, "holes": [hole(0.9)]},
                  {"courseVersion": 4, "holes": [hole(30.0)]}, {"courseVersion": 3, "holes": [hole(60.0)]}]
        pins = layout.pins_by_hole(rounds, 4, project, top_n=3)[1]
        assert pins[0]["rounds"] == 2 and len(pins) == 2  # version-3 round left out


class TestProcessLayout:
    def test_outputs_without_terrain(self, course):
        r = layout.process_layout("test")
        out = course / "output" / "layout"
        assert r["holes"] == 2 and not r["framed_to_terrain"]
        holes = json.loads((out / "holes.json").read_text())["holes"]
        h1 = holes[0]
        assert h1["par"] == 4 and h1["stroke_index"] is None
        assert len(h1["tee_boxes"]) == 2 and h1["tees"][0]["error_m"] < 5  # test coords use a rough degree size
        assert h1["surface_counts"]["bunker"] == 1
        geo = json.loads((out / "surfaces.geojson").read_text())
        assert len(geo["features"]) == 12  # 6 valid shapes x 2 holes
        assert not (out / "elevation_check.json").exists()

    def test_svg_skips_guides_and_has_opcd_colours(self, course):
        layout.process_layout("test")
        svg = (course / "output" / "layout" / "test_surfaces_opcd.svg").read_text()
        assert 'fill="#BCE5A4"' in svg and 'fill="#43E561"' in svg
        assert svg.count("golf.green-hole") == 2
        assert "fairway_center" not in svg

    def test_local_surfaces_drop_guides(self, course):
        layout.process_layout("test")
        data = json.loads((course / "output" / "layout" / "surfaces_local.json").read_text())
        assert {s["surface"] for s in data["surfaces"]} == {"green", "bunker", "fairway", "tee"}

    def test_framed_to_terrain_and_height_check(self, course):
        tif = make_terrain(course, elev=20.0)
        r = layout.process_layout("test")
        assert r["framed_to_terrain"]
        out = course / "output" / "layout"
        svg = (out / "test_surfaces_opcd.svg").read_text()
        assert 'width="400.00mm" height="500.00mm"' in svg
        check = json.loads((out / "elevation_check.json").read_text())
        assert check["overall_mean_diff_m"] == pytest.approx(0.3, abs=0.01)
        assert check["holes"]["1"]["over_tolerance"] == 0
        frame = json.loads((out / "holes.json").read_text())["frame"]
        assert frame["source"] == "terrain" and tif.exists()

    def test_missing_holes_raises(self, course):
        for f in (course / "data" / "raw" / "arccos" / "holes").glob("*.json"):
            f.unlink()
        with pytest.raises(FileNotFoundError):
            layout.process_layout("test")
