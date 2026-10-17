"""
Tests for pipeline/07_opcd_package.py

Run with:
    .venv/bin/pytest tests/test_07_opcd_package.py -v

Uses a tilted-plane terrain (height rises 0.05 m per metre north, 0.02 per metre east),
so every height and the RAW row order can be checked exactly.
"""

import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import utils  # noqa: E402

layout = importlib.import_module("05_layout")
opcd = importlib.import_module("07_opcd_package")

EPSG = 32619
W0, S0 = 344000.0, 4705000.0   # terrain tif's south-west corner


def plane(e, n):
    return 20.0 + 0.02 * (e - W0) + 0.05 * (n - S0)


@pytest.fixture
def course(tmp_path, monkeypatch):
    for m in (utils, layout, opcd):
        monkeypatch.setattr(m, "REPO_ROOT", tmp_path)
    base = tmp_path / "courses" / "test"
    (base / "output" / "layout").mkdir(parents=True)
    (base / "course.json").write_text(json.dumps({"name": "Test", "slug": "test", "holes": 1}))
    import rasterio
    from pyproj import Transformer
    from rasterio.transform import from_origin
    # 1 km square terrain at 2 m
    xs = W0 + 1 + 2 * np.arange(500)
    ys = S0 + 1000 - 1 - 2 * np.arange(500)
    dem = plane(xs[None, :], ys[:, None]).astype(np.float32)
    tif = base / "data" / "processed" / "heightmaps" / "test_terrain.tif"
    tif.parent.mkdir(parents=True)
    with rasterio.open(tif, "w", driver="GTiff", height=500, width=500, count=1, dtype="float32",
                       crs=f"EPSG:{EPSG}", transform=from_origin(W0, S0 + 1000, 2, 2)) as ds:
        ds.write(dem, 1)
    to_ll = Transformer.from_crs(EPSG, 4326, always_xy=True)

    def ring(e0, n0, e1, n1):
        pts = [to_ll.transform(e, n) for e, n in ((e0, n0), (e1, n0), (e1, n1), (e0, n1), (e0, n0))]
        return {"type": "Polygon", "coordinates": [[list(p) for p in pts]]}
    feats = [{"type": "Feature", "properties": {"hole": 1, "surface": "fairway"}, "geometry": ring(W0 + 400, S0 + 350, W0 + 440, S0 + 550)},
             {"type": "Feature", "properties": {"hole": 1, "surface": "green"}, "geometry": ring(W0 + 410, S0 + 560, W0 + 430, S0 + 580)}]
    (base / "output" / "layout" / "surfaces.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    pt = lambda e, n: {"easting": e, "northing": n}  # noqa: E731
    (base / "output" / "layout" / "holes.json").write_text(json.dumps({"epsg": EPSG, "holes": [{
        "hole": 1, "par": 4, "green": pt(W0 + 420, S0 + 620),
        "tees": [{"name": "Blue", "yards": 350, "position": pt(W0 + 420, S0 + 290)}],
        "pins": [dict(pt(W0 + 421, S0 + 622), rounds=5)],
        "penalty_areas": [{"stakes": "red", "points": [pt(W0 + 450, S0 + 400), pt(W0 + 460, S0 + 400), pt(W0 + 460, S0 + 410)]}],
    }]}))
    return base


def test_square_is_the_smallest_unity_size():
    s = [{"en": [(0, 0), (300, 0), (300, 250)]}]
    sq = opcd.choose_square(s, margin_m=100)
    assert sq["samples"] == 513 and sq["size_m"] == 512
    assert sq["west"] <= -100 and sq["east"] >= 400
    with pytest.raises(ValueError):
        opcd.choose_square([{"en": [(0, 0), (5000, 0)]}])


def test_package(course):
    r = opcd.build_package("test")
    sq, hm = r["square"], r["heightmap"]
    out = course / "output" / "opcd"
    assert sq["samples"] == 513
    raw = np.fromfile(out / "test_heightmap_513.raw", dtype="<u2").reshape(513, 513)
    z = raw.astype(float) / 65535 * hm["height_m"] + hm["base_m"]
    # first row is the south edge, first column the west edge (Unity, no flip)
    assert z[0, 0] == pytest.approx(plane(sq["west"], sq["south"]), abs=0.05)
    assert z[-1, 0] == pytest.approx(plane(sq["west"], sq["north"]), abs=0.05)
    assert z[0, -1] == pytest.approx(plane(sq["east"], sq["south"]), abs=0.05)
    assert r["share_filled"] == 0

    gk = json.loads((out / "greenkeeper.json").read_text())["holes"][0]
    tee = gk["tees"][0]
    assert tee["x"] == pytest.approx(W0 + 420 - sq["west"]) and tee["z"] == pytest.approx(S0 + 290 - sq["south"])
    assert tee["y"] == pytest.approx(plane(W0 + 420, S0 + 290) - hm["base_m"], abs=0.02)
    assert gk["penalty_areas"][0]["stakes"] == "red"
    assert "tee,Blue,350" in (out / "greenkeeper.csv").read_text()

    unity = json.loads((out / "unity_terrain.json").read_text())
    assert unity["resolution"] == 513 and unity["flip_vertically"] is False
    svg = (out / "test_clender.svg").read_text()
    assert 'width="512.00mm"' in svg and "golf.green" in svg
    assert "513" in (out / "README.md").read_text()


def test_terrain_smaller_than_the_square_is_filled(course):
    r = opcd.build_package("test", margin_m=300)  # needs 1025: more than the 1 km terrain
    assert r["square"]["samples"] == 1025 and 0 < r["share_filled"] < 0.2


def test_needs_terrain(course):
    (course / "data" / "processed" / "heightmaps" / "test_terrain.tif").unlink()
    with pytest.raises(FileNotFoundError, match="01_lidar"):
        opcd.build_package("test")
