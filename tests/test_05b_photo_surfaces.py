"""
Tests for pipeline/05b_photo_surfaces.py, plus the extra-shapes merge in 05_layout.py

Run with:
    .venv/bin/pytest tests/test_05b_photo_surfaces.py -v

Uses a synthetic 4-band aerial image (120 m square, 0.3 m pixels) with:
  - a bunker Arccos maps (sand)       -> bunker_matches_arccos
  - sand next to the fairway, unmapped -> bunker_missing_from_arccos
  - a thin grey path                  -> rejected (not a bunker)
  - an Arccos bunker on plain grass   -> arccos_bunker_no_sand_seen
  - a brown patch in the rough        -> native_grass
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
photo = importlib.import_module("05b_photo_surfaces")

E0, N0, PX, SIZE = 344000.0, 4705000.0, 0.3, 400  # image top-left (EPSG:32619) and pixel grid
TURF = (133, 150, 109, 198)
SAND = (214, 208, 198, 193)
PATH = (182, 180, 176, 150)
NATIVE = (150, 146, 105, 176)


def to_ll(e, n):
    from pyproj import Transformer
    lon, lat = Transformer.from_crs(32619, 4326, always_xy=True).transform(e, n)
    return {"latitude": lat, "longitude": lon}


def rect(e0, n0, e1, n1):
    """Arccos-style outline (lat/lon) of a rectangle in metres from the image's top-left."""
    return [to_ll(E0 + e0, N0 - n0), to_ll(E0 + e1, N0 - n0), to_ll(E0 + e1, N0 - n1), to_ll(E0 + e0, N0 - n1)]


def paint(img, e0, n0, e1, n1, colour):
    """Fills metres (e0..e1 east, n0..n1 south of top-left) with a colour."""
    img[:, int(n0 / PX):int(n1 / PX), int(e0 / PX):int(e1 / PX)] = np.array(colour, np.float32)[:, None, None]


@pytest.fixture
def course(tmp_path, monkeypatch):
    for m in (utils, layout, photo):
        monkeypatch.setattr(m, "REPO_ROOT", tmp_path)
    base = tmp_path / "courses" / "test"
    (base / "data" / "raw" / "arccos" / "holes").mkdir(parents=True)
    (base / "course.json").write_text(json.dumps({"name": "Test", "slug": "test", "holes": 1}))
    hole = {"holeId": 1, "mensPar": 4, "holeTees": [], "features": [
        {"featureId": 7, "coordinates": rect(30, 10, 60, 110)},    # fairway
        {"featureId": 1, "coordinates": rect(35, 112, 55, 118)},   # green (thin, at the bottom)
        {"featureId": 2, "coordinates": rect(65, 20, 77, 32)},     # bunker Arccos has (sand painted)
        {"featureId": 2, "coordinates": rect(10, 90, 18, 98)},     # bunker Arccos has (grass only)
    ]}
    (base / "data" / "raw" / "arccos" / "holes" / "hole_01.json").write_text(json.dumps(hole))

    rng = np.random.default_rng(1)
    img = np.zeros((4, SIZE, SIZE), np.float32)
    paint(img, 0, 0, 120, 120, TURF)
    paint(img, 65, 20, 77, 32, SAND)          # mapped bunker
    paint(img, 64, 60, 74, 70, SAND)          # unmapped bunker beside the fairway
    paint(img, 20, 0, 21.5, 80, PATH)         # thin path
    paint(img, 2, 40, 22, 60, NATIVE)         # native patch
    img += rng.normal(0, 3, img.shape).astype(np.float32)

    import rasterio
    from rasterio.transform import from_origin
    tif = base / "data" / "processed" / "textures" / "test_imagery.tif"
    tif.parent.mkdir(parents=True)
    with rasterio.open(tif, "w", driver="GTiff", height=SIZE, width=SIZE, count=4, dtype="uint8",
                       crs="EPSG:32619", transform=from_origin(E0, N0, PX, PX)) as ds:
        ds.write(np.clip(img, 0, 255).astype(np.uint8))
    return base


class TestHelpers:
    def test_simplify_ring_keeps_a_square(self):
        ring = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2), (1, 2), (0, 2), (0, 1), (0, 0)]
        out = photo.simplify_ring(ring, 0.1)
        assert sorted(out) == [(0, 0), (0, 2), (2, 0), (2, 2)]

    def test_limits_fall_back_without_enough_bunkers(self):
        lim = photo.learn_limits([{"arccos_overlap": 1, "nir": 200, "elong": 1, "fill": 0.9, "turf_gap_m": 1}])
        assert lim["learned_from"] == 0 and lim["nir_min"] == photo.DEFAULTS["nir_min"]

    def test_limits_learned_from_matching_bunkers(self):
        known = [{"arccos_overlap": 1, "nir": 190 + i, "elong": 1.5, "fill": 0.6, "turf_gap_m": 2} for i in range(10)]
        lim = photo.learn_limits(known)
        assert lim["learned_from"] == 10 and lim["nir_min"] == pytest.approx(185.45, abs=0.1)

    def test_path_like_shape_is_not_a_bunker(self):
        lim = dict(photo.DEFAULTS)
        assert not photo.is_bunker({"nir": 150, "elong": 1.2, "fill": 0.6, "turf_gap_m": 2}, lim)  # too little infrared
        assert not photo.is_bunker({"nir": 195, "elong": 8.0, "fill": 0.6, "turf_gap_m": 2}, lim)  # long and thin
        assert photo.is_bunker({"nir": 195, "elong": 1.5, "fill": 0.6, "turf_gap_m": 2}, lim)


class TestProcess:
    def test_findings(self, course):
        r = photo.process("test", overlays=False)
        c = r["counts"]
        assert c.get("bunker_matches_arccos") == 1
        assert c.get("bunker_missing_from_arccos") == 1
        assert c.get("arccos_bunker_no_sand_seen") == 1
        assert c.get("native_grass", 0) >= 1
        assert r["sand_model"]["learned"]

    def test_missing_bunker_is_where_the_sand_is(self, course):
        photo.process("test", overlays=False)
        geo = json.loads((course / "output" / "layout" / "photo_surfaces.geojson").read_text())
        missing = [f for f in geo["features"] if f["properties"]["finding"] == "bunker_missing_from_arccos"][0]
        from pyproj import Transformer
        t = Transformer.from_crs(4326, 32619, always_xy=True)
        pts = [t.transform(x, y) for x, y in missing["geometry"]["coordinates"][0]]
        ce = np.mean([p[0] for p in pts]) - E0
        cs = N0 - np.mean([p[1] for p in pts])
        assert ce == pytest.approx(69, abs=1.5) and cs == pytest.approx(65, abs=1.5)
        assert missing["properties"]["area_m2"] == pytest.approx(100, rel=0.2)

    def test_extras_merge_into_layout(self, course):
        photo.process("test", write_extras=True, overlays=False)
        extras = json.loads((course / "extra_surfaces.geojson").read_text())["features"]
        assert {f["properties"]["surface"] for f in extras} == {"bunker", "native"}
        r = layout.process_layout("test")
        assert r["extra_surfaces"] == len(extras)
        geo = json.loads((course / "output" / "layout" / "surfaces.geojson").read_text())
        assert any(f["properties"]["surface"] == "native" for f in geo["features"])

    def test_write_extras_keeps_your_entries(self, course):
        mine = {"type": "Feature", "properties": {"surface": "penalty_area", "hole": 1, "source": "member correction"},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}}
        (course / "extra_surfaces.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [mine]}))
        photo.process("test", write_extras=True, overlays=False)
        photo.process("test", write_extras=True, overlays=False)  # twice: photo shapes replaced, not stacked
        extras = json.loads((course / "extra_surfaces.geojson").read_text())["features"]
        assert sum(f["properties"].get("source") == "member correction" for f in extras) == 1
        assert sum(f["properties"].get("finding") == "bunker_missing_from_arccos" for f in extras) == 1

    def test_tree_mask_finds_woods(self):
        rng = np.random.default_rng(2)
        img = np.zeros((4, 300, 300), np.float32)
        img[:] = np.array(TURF, np.float32)[:, None, None]
        img[:, :, 200:] = np.array((60, 80, 55, 150), np.float32)[:, None, None]  # dark, rough-textured woods
        img += rng.normal(0, 3, img.shape).astype(np.float32)
        img[:, :, 200:] += rng.normal(0, 25, (4, 300, 100)).astype(np.float32)
        lab = np.zeros((300, 300), np.int16)
        lab[:, :120] = photo.LABELS["turf"]
        lab[:, 260:] = photo.LABELS["trees"]
        mask, _ = photo.tree_mask(np.clip(img, 0, 255), lab, PX)
        assert mask[:, 210:255].mean() > 0.8 and mask[:, :120].mean() < 0.05

    def test_green_grows_to_its_real_edge(self):
        rng = np.random.default_rng(3)
        y, x = np.mgrid[:200, :200]
        r = np.hypot(x - 100, y - 100) * PX
        img = np.zeros((4, 200, 200), np.float32)
        img[:] = np.array(TURF, np.float32)[:, None, None]
        img[:, r < 12] = np.array((165, 185, 140, 210), np.float32)[:, None]  # real green, radius 12 m
        img += rng.normal(0, 3, img.shape).astype(np.float32)
        arccos = r < 8                                                       # Arccos drew it too small
        g = photo.green_outline(img, np.zeros((200, 200), np.uint8), arccos, PX)
        assert g.sum() * PX * PX == pytest.approx(np.pi * 12 ** 2, rel=0.12)
        assert g[arccos].all()

    def test_green_under_tree_shadow_is_filled(self):
        rng = np.random.default_rng(4)
        y, x = np.mgrid[:200, :200]
        r = np.hypot(x - 100, y - 100) * PX
        img = np.zeros((4, 200, 200), np.float32)
        img[:] = np.array(TURF, np.float32)[:, None, None]
        img[:, r < 12] = np.array((165, 185, 140, 210), np.float32)[:, None]
        img[:, (x > 125) & (abs(y - 100) < 25)] *= 0.45                       # shadow over the green's right side
        img += rng.normal(0, 3, img.shape).astype(np.float32)
        g = photo.green_outline(img, np.zeros((200, 200), np.uint8), r < 8, PX)
        assert g.sum() * PX * PX == pytest.approx(np.pi * 12 ** 2, rel=0.12)
        assert g[100, 131]  # 9.3 m right of centre, under the shadow (the watershed alone stops at 6.9 m)

    def test_shadows_and_roofs_are_not_trees(self):
        rng = np.random.default_rng(5)
        img = np.zeros((4, 300, 300), np.float32)
        img[:] = np.array(TURF, np.float32)[:, None, None]
        lab = np.full((300, 300), photo.LABELS["turf"], np.uint8)
        y, x = np.mgrid[:300, :300]
        canopy = np.array((60, 85, 55, 165), np.float32)[:, None]
        for cy, cx in ((80, 150), (220, 100), (220, 220)):  # three Arccos trees, each with a shadow to the west
            t = np.hypot(x - cx, y - cy) < 15
            img[:, t] = canopy
            lab[t] = photo.LABELS["trees"]
            sh = (np.hypot(x - cx + 20, y - cy) < 15) & ~t
            img[:, sh] = np.array((55, 70, 45, 110), np.float32)[:, None]
        roof = (abs(x - 40) < 10) & (abs(y - 40) < 10)
        img[:, roof] = np.array((70, 72, 75, 72), np.float32)[:, None]
        real = np.hypot(x - 260, y - 40) < 12  # a tree Arccos missed, no shadow drawn
        img[:, real] = canopy
        crowns = (lab == photo.LABELS["trees"]) | real
        img += rng.normal(0, 2, img.shape).astype(np.float32)
        img[:, crowns] += rng.normal(0, 22, (4, int(crowns.sum()))).astype(np.float32)  # crowns are patchy
        shadow = (np.hypot(x - 130, y - 80) < 15) & (lab != photo.LABELS["trees"])
        found = shadow | roof | real
        kept, info = photo.drop_shadows_and_roofs(img, lab, found, PX)
        assert info["shadow_direction_row_col"] == [0.0, -1.0]  # shadows fall west
        assert kept[real].mean() > 0.95
        assert kept[shadow].mean() < 0.25 and not kept[roof].any()  # slivers left go in the clean-up after

    def test_fairway_runup_joins_the_green(self):
        # Arccos fairway stops 15 m short of the green; the photo shows mown grass all the way
        import rasterio
        rng = np.random.default_rng(6)
        img = np.zeros((4, 400, 400), np.float32)
        img[:] = np.array((122, 136, 99, 180), np.float32)[:, None, None]       # rough
        img += rng.normal(0, 6, img.shape).astype(np.float32)                  # rough is patchy
        mown = np.array((132, 150, 108, 199), np.float32)[:, None, None]
        img[:, 40:360, 160:240] = mown + rng.normal(0, 1.5, (4, 320, 80)).astype(np.float32)  # fairway + run-up
        tf = rasterio.transform.from_origin(E0, N0, PX, PX)
        en = lambda c, r: (E0 + c * PX, N0 - r * PX)  # noqa: E731
        fairway = ([en(160 + c, 120) for c in range(0, 80, 10)] + [en(240, 120 + r) for r in range(0, 180, 10)]
                   + [en(240 - c, 300) for c in range(0, 80, 10)] + [en(160, 300 - r) for r in range(0, 180, 10)])  # rows 120-300
        green = [en(170, 350), en(230, 350), en(230, 395), en(170, 395)]     # rows 350-395, the run-up is 300-350
        surfaces = [{"hole": 1, "surface": "fairway", "en": fairway}, {"hole": 1, "surface": "green", "en": green}]
        lab = np.zeros((400, 400), np.uint8)
        out = photo.fairway_runups(img, tf, lab, surfaces, PX)
        assert len(out) == 1 and out[0]["added_m2"] > 0.8 * 15 * 24  # most of the 15 m x 24 m gap
        ring = max(out[0]["rings"], key=len)
        rows = [(N0 - n) / PX for _, n in ring]
        assert max(rows) > 345 and min(rows) < 125  # one shape, from the fairway's top to the green

    def test_photo_outlines_replace_and_drop(self, course):
        photo.process("test", overlays=False)
        cfg = json.loads((course / "course.json").read_text())
        cfg["layout"] = {"bunkers_from_photo": True, "drop_bunkers_without_sand": True}
        (course / "course.json").write_text(json.dumps(cfg))
        r = layout.process_layout("test")
        assert r["photo_bunkers"] == {"replaced": 1, "dropped": 1}
        geo = json.loads((course / "output" / "layout" / "surfaces.geojson").read_text())
        bunkers = [f for f in geo["features"] if f["properties"]["surface"] == "bunker"]
        assert len(bunkers) == 1  # the grass-only Arccos bunker is gone

    def test_puttview_outline_replaces_green(self, course):
        # PuttView page for hole 1: a 22 x 8 yd green (the Arccos one is 20 x 6 m), on its side
        grids = course / "data" / "processed" / "grids"
        grids.mkdir(parents=True)
        (grids / "hole_01_green.json").write_text(json.dumps(
            {"hole": 1, "outline_yards": [[0, 0], [8, 0], [8, 22], [0, 22]], "points": []}))
        r = layout.process_layout("test")
        assert r["puttview_greens"] == 1
        h = json.loads((course / "output" / "layout" / "holes.json").read_text())["holes"][0]
        fit = h["green"]["puttview_fit"]
        assert fit["rotation_deg"] % 180 == pytest.approx(90, abs=4)
        geo = json.loads((course / "output" / "layout" / "surfaces.geojson").read_text())
        green = [f for f in geo["features"] if f["properties"]["surface"] == "green"][0]
        from pyproj import Transformer
        t = Transformer.from_crs(4326, 32619, always_xy=True)
        pts = np.array([t.transform(x, y) for x, y in green["geometry"]["coordinates"][0]])
        assert np.ptp(pts[:, 0]) == pytest.approx(22 * 0.9144, abs=0.3)  # east-west, like the Arccos green

    def test_photo_outlines_off_by_default(self, course):
        photo.process("test", overlays=False)
        assert layout.process_layout("test")["photo_bunkers"] == {"replaced": 0, "dropped": 0}

    def test_correction_removes_bunker_and_adds_penalty_area(self, course):
        from pyproj import Transformer
        t = Transformer.from_crs(32619, 4326, always_xy=True)
        ring = [list(t.transform(E0 + e, N0 - n)) for e, n in ((8, 88), (20, 88), (20, 100), (8, 100), (8, 88))]
        (course / "extra_surfaces.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"action": "remove", "surface": "bunker", "hole": 1},
             "geometry": {"type": "Polygon", "coordinates": [ring]}},
            {"type": "Feature", "properties": {"surface": "penalty_area", "hole": 1, "note": "staked red"},
             "geometry": {"type": "Polygon", "coordinates": [ring]}}]}))
        r = layout.process_layout("test")
        assert r["removed_by_corrections"] == 1
        h = json.loads((course / "output" / "layout" / "holes.json").read_text())["holes"][0]
        assert h["surface_counts"]["bunker"] == 1 and h["penalty_areas"][0]["note"] == "staked red"
        svg = (course / "output" / "layout" / "test_surfaces_opcd.svg").read_text()
        assert "penalty" not in svg  # set in GreenKeeper, not drawn as a surface

    def test_curved_shape_overlap(self):
        crescent = [(0, 0), (10, 0), (10, 10), (8, 10), (8, 2), (0, 2)]  # centre falls outside it
        assert not layout._inside(*layout.centroid(crescent), crescent)
        assert layout.overlaps(crescent, [(x * 1.05, y * 1.05) for x, y in crescent])

    def test_overlays_written(self, course):
        photo.process("test")
        assert (course / "output" / "layout" / "photo_check" / "hole_01.jpg").exists()

    def test_needs_four_bands(self, course):
        import rasterio
        from rasterio.transform import from_origin
        tif = course / "data" / "processed" / "textures" / "test_imagery.tif"
        with rasterio.open(tif, "w", driver="GTiff", height=10, width=10, count=3, dtype="uint8",
                           crs="EPSG:32619", transform=from_origin(E0, N0, PX, PX)) as ds:
            ds.write(np.zeros((3, 10, 10), np.uint8))
        with pytest.raises(ValueError):
            photo.process("test", overlays=False)
