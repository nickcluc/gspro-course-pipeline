"""
Tests for pipeline/06_blender_assembly.py (the plan; Blender itself isn't needed)

Run with:
    .venv/bin/pytest tests/test_06_blender_assembly.py -v
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

asm = importlib.import_module("06_blender_assembly")


def plane(x, n):
    """Terrain height used by the fixtures: a tilted plane."""
    return 10.0 + 0.02 * x + 0.05 * n


def write_terrain_obj(path: Path, half: int = 300, step: int = 10) -> Path:
    """Stage 04 style grid OBJ: x = east, y = height, z = north."""
    xs = list(range(-half, half + 1, step))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for n in xs:
            for x in xs:
                f.write(f"v {x} {plane(x, n):.4f} {n}\n")
        k = len(xs)
        for r in range(k - 1):
            for c in range(k - 1):
                a = r * k + c + 1
                f.write(f"f {a} {a + k} {a + 1}\nf {a + 1} {a + k} {a + k + 1}\n")
    return path


L_SHAPE = [(0, 0), (30, 0), (30, 10), (10, 10), (10, 30), (0, 30)]  # metres, not symmetric


def turn(points, deg, about=(0, 0), to=(0, 0)):
    t = math.radians(deg)
    out = []
    for x, y in points:
        x, y = x - about[0], y - about[1]
        out.append((x * math.cos(t) - y * math.sin(t) + to[0], x * math.sin(t) + y * math.cos(t) + to[1]))
    return out


def grid_in(poly, step=1.0):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return np.array([(x, y) for x in np.arange(min(xs), max(xs), step) for y in np.arange(min(ys), max(ys), step)
                     if asm.inside(x + 0.5, y + 0.5, poly)]) + 0.5


@pytest.fixture
def course(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(asm, "REPO_ROOT", tmp_path)
    base = tmp_path / "courses" / "test"
    base.mkdir(parents=True)
    (base / "course.json").write_text(json.dumps({"name": "Test", "slug": "test", "holes": 1}))
    out = base / "output"
    write_terrain_obj(out / "terrain" / "test_terrain.obj")
    green = turn(L_SHAPE, 90, about=(15, 15), to=(0, 100))  # Arccos green, turned 90 degrees
    (out / "layout").mkdir(parents=True)
    (out / "layout" / "surfaces_local.json").write_text(json.dumps({"surfaces": [
        {"hole": 1, "surface": "green", "points": green},
        {"hole": 1, "surface": "bunker", "points": [(20, 90), (25, 90), (25, 95), (20, 95)]},
        {"hole": 1, "surface": "bunker", "points": [(-25, 90), (-20, 90), (-20, 95), (-25, 95)]},
        {"hole": 1, "surface": "fairway", "points": [(-15, -100), (15, -100), (15, 60), (-15, 60)]},
        {"hole": 1, "surface": "out_of_bounds", "points": [(0, 0), (1, 0), (1, 1)]},
    ]}))
    (out / "layout" / "holes.json").write_text(json.dumps({"holes": [{
        "hole": 1, "green": {"x": 0, "z": 100},
        "tees": [{"name": "Blue", "position": {"x": 0, "z": -200}},
                 {"name": "Black", "position": {"x": 0, "z": -230}}],
        "pins": [{"x": 2, "z": 101, "rounds": 5}],
    }]}))
    # PuttView green: the L shape, north-up, in OBJ axes (x, height, z = -grid y)
    pts = grid_in(L_SHAPE)
    (out / "greens").mkdir()
    with open(out / "greens" / "hole_01_green.obj", "w") as f:
        for x, y in pts:
            f.write(f"v {x:.3f} {0.1 * x:.3f} {-y:.3f}\n")
    grids = base / "data" / "processed" / "grids"
    grids.mkdir(parents=True)
    (grids / "hole_01_green.json").write_text(json.dumps(
        {"hole": 1, "points": [{"x": x / 0.9144, "y": y / 0.9144, "elevation_ft": 0} for x, y in pts]}))
    return base


class TestTerrain:
    def test_sampler_is_exact_on_a_plane(self, tmp_path):
        v, _ = asm.read_obj(write_terrain_obj(tmp_path / "t.obj"))
        s = asm.TerrainSampler(v)
        for x, n in ((0, 0), (12.5, -37.2), (299, 299)):
            assert s.height(x, n) == pytest.approx(plane(x, n), abs=1e-6)

    def test_sampler_clamps_outside(self, tmp_path):
        v, _ = asm.read_obj(write_terrain_obj(tmp_path / "t.obj"))
        assert asm.TerrainSampler(v).height(10_000, 0) == pytest.approx(plane(300, 0))

    def test_terrain_axes_become_east_north_up(self, tmp_path):
        v, f = asm.read_obj(write_terrain_obj(tmp_path / "t.obj", half=10))
        m = asm.terrain_mesh(v, f)
        x, n, z = m["verts"][0]
        assert z == pytest.approx(plane(x, n), abs=1e-4)


class TestPolygons:
    def test_densify_limits_edge_length(self):
        ring = asm.densify([[0, 0], [10, 0], [10, 10]], 3.0)
        edges = [math.dist(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))]
        assert max(edges) <= 3.0 + 1e-9 and ring[0] == (0, 0)

    def test_centre_of_square(self):
        assert asm.polygon_centre([(0, 0), (4, 0), (4, 4), (0, 4)]) == pytest.approx((2, 2))


class TestGreens:
    def test_fit_finds_the_turn(self):
        outline = turn(L_SHAPE, 90, about=(15, 15), to=(0, 100))
        fit = asm.fit_green(grid_in(L_SHAPE), outline)
        assert fit["rotation_deg"] == pytest.approx(90) and fit["fit"] > 0.9

    def test_place_green_moves_pivot_and_sets_height(self):
        v = np.array([[0, 1.0, 0], [2, 3.0, 0], [0, 2.0, -2], [2, 2.0, -2]])
        out = asm.place_green(v, 90, (1, 1), (50, 60), 20.0)
        assert out[:, :2].mean(axis=0) == pytest.approx((50, 60))
        assert out[:, 2].mean() == pytest.approx(20.0)
        # (2, 0) is 1 east and 1 south of the pivot; a 90 degree counter-clockwise turn makes it 1 east, 1 north
        assert out[1, :2] == pytest.approx((51, 61))

    def test_footprint_prefers_grid_points(self, tmp_path):
        g = tmp_path / "g.json"
        g.write_text(json.dumps({"points": [{"x": 1, "y": 2, "elevation_ft": 0}]}))
        assert asm.green_footprint(g, np.zeros((4, 3))) == pytest.approx(np.array([[0.9144, 1.8288]]))


class TestPlan:
    def test_plan_names_and_counts(self, course):
        plan = asm.plan_scene("test")
        names = [s["name"] for s in plan["surfaces"]]
        assert names == ["h01_green", "h01_bunker_01", "h01_bunker_02", "h01_fairway"]
        assert plan["report"]["counts"] == {"surfaces": 4, "puttview_greens": 1, "markers": 3, "cameras": 1}

    def test_surfaces_are_draped(self, course):
        plan = asm.plan_scene("test")
        for x, y, z in plan["surfaces"][3]["outline"]:
            assert z == pytest.approx(plane(x, y) + asm.lift("fairway"), abs=1e-6)

    def test_duplicate_shapes_on_two_holes_kept_once(self, course):
        f = course / "output" / "layout" / "surfaces_local.json"
        data = json.loads(f.read_text())
        pond = [(50, 0), (60, 0), (60, 10), (50, 10)]
        data["surfaces"] += [{"hole": 1, "surface": "water", "points": pond},
                             {"hole": 2, "surface": "water", "points": pond[1:] + pond[:1]}]
        f.write_text(json.dumps(data))
        plan = asm.plan_scene("test")
        assert [s["name"] for s in plan["surfaces"] if s["surface"] == "water"] == ["h01_water"]
        assert plan["report"]["duplicate_shapes_skipped"] == 1

    def test_surface_layers_stack(self):
        assert asm.lift("fairway") < asm.lift("green") < asm.lift("bunker") < asm.lift("water")

    def test_green_turned_and_seated(self, course):
        plan = asm.plan_scene("test")
        rep = plan["report"]["holes"][1]
        assert rep["rotation_deg"] == pytest.approx(90) and rep["fit"] > 0.9
        g = np.array(plan["greens"][0]["verts"])
        assert g[:, :2].mean(axis=0) == pytest.approx(asm.polygon_centre(
            turn(L_SHAPE, 90, about=(15, 15), to=(0, 100))), abs=0.5)

    def test_rotation_override_from_config(self, course):
        cfg = json.loads((course / "course.json").read_text())
        cfg["blender"] = {"green_rotation_deg": {"1": 45}}
        (course / "course.json").write_text(json.dumps(cfg))
        rep = asm.plan_scene("test")["report"]["holes"][1]
        assert rep["rotation_deg"] == 45 and rep["rotation_set_by"] == "course.json"

    def test_camera_behind_back_tee_looking_at_green(self, course):
        cam = asm.plan_scene("test")["cameras"][0]
        assert cam["at"][1] == pytest.approx(-230 - asm.CAMERA_BACK_M)
        assert cam["at"][2] == pytest.approx(plane(0, -230) + asm.CAMERA_UP_M)
        assert cam["look_at"][:2] == (0, 100)
        assert cam["top_turn_rad"] == pytest.approx(0)  # hole runs north: no turn needed

    def test_missing_inputs_raise(self, course):
        (course / "output" / "layout" / "holes.json").unlink()
        with pytest.raises(FileNotFoundError):
            asm.plan_scene("test")
