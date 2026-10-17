"""
05b_photo_surfaces.py — Find bunkers and native grass in the aerial photo

Checks the Arccos course map (Stage 00) against the NAIP aerial image
(Stage 03, red/green/blue + near-infrared, 0.3–0.6 m):

  1. Bunkers Arccos is missing (sand in the photo, no Arccos bunker there)
  2. Photo outlines for the bunkers Arccos has (Arccos outlines are often
     smaller than the sand, or a few metres off)
  3. Arccos bunkers where the photo shows no sand (grassed over, or misplaced)
  4. Native / long-grass areas, which Arccos doesn't map at all
  5. Woods Arccos doesn't map as trees (tree-lined holes play as rough otherwise)

It calibrates itself on each course and photo: what sand looks like, and how
big and how far from turf a bunker is, are learned from the pixels inside
the bunkers Arccos already maps. That keeps it working across photo dates,
light and turf colour, where fixed thresholds don't.

Usage:
    python pipeline/05b_photo_surfaces.py --course tedesco
    python pipeline/05b_photo_surfaces.py --course tedesco --write-extras

Input:
    courses/<slug>/data/raw/arccos/holes/hole_XX.json             (Stage 00)
    courses/<slug>/data/processed/textures/<slug>_imagery.tif     (Stage 03, 4 bands)

Output:
    courses/<slug>/output/layout/photo_surfaces.geojson   every finding, WGS84
    courses/<slug>/output/layout/photo_check.json         what was learned, counts per hole
    courses/<slug>/output/layout/photo_check/hole_XX.jpg  photo with the findings drawn on
    courses/<slug>/extra_surfaces.geojson                 (--write-extras) new bunkers, native
        areas and trees, to review and edit in QGIS; Stage 05 adds every shape in it.
        Your own entries (any source other than "aerial photo") are kept on rewrite.

Overlay colours: yellow = Arccos bunker, green = sand matching it, red = bunker
Arccos is missing, purple = native grass, blue fill = trees Arccos is missing,
white = fairway, blue line = green.

Dependencies:
    pip install rasterio numpy scipy pyproj pillow
"""

import argparse
import importlib
import json
import math
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from utils import REPO_ROOT, load_course_config

layout = importlib.import_module("05_layout")

# Starting values; replaced by values learned from the course's own bunkers
DEFAULTS = {"sand_distance_max": 4.0, "nir_min": 174.0, "elong_max": 3.8, "fill_min": 0.30,
            "turf_gap_max_m": 15.5}
MIN_BUNKER_M2, MAX_BUNKER_M2, MIN_BUNKER_WIDTH_M = 15.0, 3000.0, 2.5
MATCH_OVERLAP = 0.2           # share of a sand area within 3 m of an Arccos bunker to count as a match
SEARCH_FROM_TURF_M = 40.0     # bunkers are searched for within this distance of fairways, greens, tees
NATIVE_FROM_COURSE_M = 35.0
MIN_NATIVE_M2 = 150.0
SIMPLIFY_M = 0.5              # outline simplification tolerance
LABELS = {"turf": 1, "sand": 2, "trees": 3, "water": 4}


def get_paths(slug: str) -> dict:
    base = REPO_ROOT / "courses" / slug
    return {
        "holes": base / "data" / "raw" / "arccos" / "holes",
        "imagery": base / "data" / "processed" / "textures" / f"{slug}_imagery.tif",
        "out": base / "output" / "layout",
        "extras": base / "extra_surfaces.geojson",
    }


# ---------------------------------------------------------------------------
# Image and labels
# ---------------------------------------------------------------------------

def read_window(tif: Path, surfaces: list[dict], pad_m: float = 60.0):
    """The 4-band image around the course, as float32 (4, H, W), plus its transform."""
    import rasterio
    from rasterio.windows import from_bounds
    with rasterio.open(tif) as ds:
        if ds.count < 4:
            raise ValueError(f"{tif} has {ds.count} bands; this stage needs red, green, blue and near-infrared.")
        es = [e for s in surfaces for e, _ in s["en"]]
        ns = [n for s in surfaces for _, n in s["en"]]
        win = from_bounds(min(es) - pad_m, min(ns) - pad_m, max(es) + pad_m, max(ns) + pad_m, ds.transform)
        win = win.intersection(rasterio.windows.Window(0, 0, ds.width, ds.height)).round_offsets().round_lengths()
        img = ds.read((1, 2, 3, 4), window=win).astype(np.float32)
        return img, ds.window_transform(win), ds.crs


def label_raster(surfaces: list[dict], transform, shape) -> np.ndarray:
    """Arccos surfaces burned into a label grid (later surfaces win)."""
    from rasterio.features import rasterize
    lab = np.zeros(shape, np.uint8)
    for surface, cls in (("trees", "trees"), ("fairway", "turf"), ("tee", "turf"), ("green", "turf"),
                         ("bunker", "sand"), ("water", "water")):
        geoms = [({"type": "Polygon", "coordinates": [s["en"] + [s["en"][0]]]}, LABELS[cls])
                 for s in surfaces if s["surface"] == surface]
        if geoms:
            m = rasterize(geoms, out_shape=shape, transform=transform, fill=0, dtype="uint8")
            lab[m > 0] = m[m > 0]
    return lab


def pixel_size(transform) -> float:
    return abs(transform.a)


# ---------------------------------------------------------------------------
# Sand
# ---------------------------------------------------------------------------

def sand_model(img: np.ndarray, lab: np.ndarray) -> dict:
    """Mean and spread of clean sand (bright, no plant signal) inside Arccos bunkers."""
    r, g, b, n = img
    ndvi = (n - r) / (n + r + 1e-6) * 100
    bright = (r + g + b) / 3
    clean = (lab == LABELS["sand"]) & (ndvi < 8) & (bright > 170)
    X = np.stack([r, g, b, n, ndvi])
    if clean.sum() < 200:  # too few known bunkers: a typical bright-sand signature
        mu = np.array([214.0, 208.0, 198.0, 193.0, -5.0])
        cov = np.diag([80.0, 70.0, 100.0, 140.0, 6.0])
        learned = False
    else:
        mu = X[:, clean].mean(1)
        cov = np.cov(X[:, clean])
        learned = True
    cov = cov + np.eye(5) * 4  # keeps the model from being too tight on a uniform bunker set
    return {"mean": mu.tolist(), "cov": cov.tolist(), "learned": learned, "pixels": int(clean.sum())}


def sand_distance(img: np.ndarray, model: dict, chunk: int = 2_000_000) -> np.ndarray:
    """Statistical distance of every pixel from the sand model (lower = more sand-like)."""
    r, g, b, n = img
    ndvi = (n - r) / (n + r + 1e-6) * 100
    X = np.stack([r, g, b, n, ndvi]).reshape(5, -1).T.astype(np.float32)
    mu = np.array(model["mean"], np.float32)
    ic = np.linalg.inv(np.array(model["cov"])).astype(np.float32)
    out = np.empty(len(X), np.float32)
    for i in range(0, len(X), chunk):
        d = X[i:i + chunk] - mu
        out[i:i + chunk] = np.sqrt(np.einsum("ij,jk,ik->i", d, ic, d))
    return out.reshape(r.shape)


def sand_areas(img, lab, dist, px, lim) -> list[dict]:
    """Sand-like patches near turf, with the measures used to accept or reject them."""
    r, g, b, n = img
    sand = dist < lim["sand_distance_max"]
    sand = ndi.binary_closing(ndi.binary_opening(sand, iterations=2), iterations=2)
    turf = lab == LABELS["turf"]
    near_turf = ndi.binary_dilation(turf, iterations=int(SEARCH_FROM_TURF_M / px))
    comp, k = ndi.label(sand & near_turf)
    if k == 0:
        return [], comp
    idx = range(1, k + 1)
    count = ndi.sum(np.ones_like(comp, np.float32), comp, index=idx)
    widest = ndi.maximum(ndi.distance_transform_edt(sand), comp, index=idx) * px * 2
    arccos_near = ndi.binary_dilation(lab == LABELS["sand"], iterations=max(1, int(3 / px)))
    overlap = ndi.sum(arccos_near, comp, index=idx) / count
    nir = ndi.mean(n, comp, index=idx)
    turf_gap = ndi.minimum(ndi.distance_transform_edt(~turf) * px, comp, index=idx)
    areas = []
    for i, sl in enumerate(ndi.find_objects(comp)):
        a = count[i] * px * px
        if a < MIN_BUNKER_M2 or a > MAX_BUNKER_M2 or widest[i] < MIN_BUNKER_WIDTH_M:
            continue
        mask = comp[sl] == i + 1
        ys, xs = np.nonzero(mask)
        if len(xs) > 2:
            ev = np.linalg.eigvalsh(np.cov(np.vstack([xs, ys])))
            elong = float(math.sqrt(ev[-1] / max(ev[0], 1e-6)))
        else:
            elong = 1.0
        box = (sl[0].stop - sl[0].start) * (sl[1].stop - sl[1].start)
        areas.append({"id": i + 1, "slice": sl, "area_m2": round(float(a), 1), "width_m": round(float(widest[i]), 1),
                      "fill": round(float(mask.sum() / box), 2), "elong": round(elong, 2),
                      "nir": round(float(nir[i]), 1), "turf_gap_m": round(float(turf_gap[i]), 1),
                      "arccos_overlap": round(float(overlap[i]), 2)})
    return areas, comp


def learn_limits(areas: list[dict]) -> dict:
    """Bunker limits from the sand areas that match Arccos bunkers (falls back to DEFAULTS)."""
    known = [a for a in areas if a["arccos_overlap"] >= MATCH_OVERLAP]
    lim = dict(DEFAULTS)
    if len(known) >= 8:
        q = lambda k, p: float(np.percentile([a[k] for a in known], p))  # noqa: E731
        lim.update(nir_min=q("nir", 5) - 5, elong_max=q("elong", 95) + 0.5,
                   fill_min=q("fill", 5) - 0.05, turf_gap_max_m=q("turf_gap_m", 95) + 5)
        lim["learned_from"] = len(known)
    else:
        lim["learned_from"] = 0
    return lim


def is_bunker(a: dict, lim: dict) -> bool:
    return (a["nir"] >= lim["nir_min"] and a["elong"] <= lim["elong_max"]
            and a["fill"] >= lim["fill_min"] and a["turf_gap_m"] <= lim["turf_gap_max_m"])


# ---------------------------------------------------------------------------
# Trees
# ---------------------------------------------------------------------------

TREE_FROM_TURF_M = 60.0   # woods are mapped this far from fairways, greens and tees
MIN_TREE_M2 = 100.0
TREE_TILE_M = 60.0        # big woods are cut into tiles so each outline stays small


def _gauss_loglik(F: np.ndarray, mu: np.ndarray, cov: np.ndarray, chunk: int = 2_000_000) -> np.ndarray:
    ic = np.linalg.inv(cov).astype(np.float32)
    ld = float(np.linalg.slogdet(cov)[1])
    X = F.reshape(F.shape[0], -1).T - mu.astype(np.float32)
    out = np.empty(len(X), np.float32)
    for i in range(0, len(X), chunk):
        d = X[i:i + chunk]
        out[i:i + chunk] = -0.5 * (np.einsum("ij,jk,ik->i", d, ic, d) + ld)
    return out.reshape(F.shape[1:])


def tree_mask(img, lab, px) -> tuple[np.ndarray, dict]:
    """
    Tree canopy, learned from the course itself: three models (Arccos tree areas,
    fairway turf, and the rough strip 2–8 m outside fairways) on brightness, plant
    signal, near-infrared and texture. A pixel is a tree where the tree model beats
    both grass models. Texture keeps tree shadows on grass (smooth) from passing as trees.
    """
    r, g, b, n = img
    bright = (r + g + b) / 3
    ndvi = (n - r) / (n + r + 1e-6) * 100
    tex = np.sqrt(np.maximum(ndi.uniform_filter(bright ** 2, 7) - ndi.uniform_filter(bright, 7) ** 2, 0))
    sig = 1.0 / px
    F = np.stack([ndi.gaussian_filter(v, sig) for v in (bright, ndvi, n, tex)]).astype(np.float32)
    trees = ndi.binary_erosion(lab == LABELS["trees"], iterations=5)
    turf = ndi.binary_erosion(lab == LABELS["turf"], iterations=5)
    fw = lab == LABELS["turf"]
    rough = (ndi.binary_dilation(fw, iterations=int(8 / px)) & ~ndi.binary_dilation(fw, iterations=int(2 / px))
             & (lab == 0) & ~ndi.binary_dilation(lab == LABELS["trees"], iterations=int(3 / px)))
    if min(trees.sum(), turf.sum(), rough.sum()) < 500:
        return np.zeros(lab.shape, bool), {}

    def model(m):
        X = F[:, m][:, ::7]
        return X.mean(1), np.cov(X) + np.eye(4) * 1e-3

    (mt, ct), (mg, cg), (mr, cr) = model(trees), model(turf), model(rough)
    tm = _gauss_loglik(F, mt, ct) > np.maximum(_gauss_loglik(F, mg, cg), _gauss_loglik(F, mr, cr))
    tm = ndi.binary_closing(ndi.binary_opening(tm, iterations=3), iterations=3)
    holes, k = ndi.label(~tm)  # fill small gaps in the canopy, but not enclosed rough corridors
    if k:
        small = ndi.sum(np.ones_like(holes), holes, index=range(1, k + 1)) * px * px < 50
        tm |= np.isin(holes, np.nonzero(small)[0] + 1)
    tm &= ~ndi.binary_dilation(np.isin(lab, (LABELS["turf"], LABELS["sand"], LABELS["water"])),
                               iterations=max(1, int(1.0 / px)))
    tm &= ndi.binary_dilation(lab == LABELS["turf"], iterations=int(TREE_FROM_TURF_M / px))
    info = {"tree_model": np.round(mt, 1).tolist(), "turf_model": np.round(mg, 1).tolist(),
            "rough_model": np.round(mr, 1).tolist(),
            "share_of_arccos_trees_found": round(float(tm[lab == LABELS["trees"]].mean()), 3)}
    return tm, info


def tiles(mask: np.ndarray, px: float, size_m: float):
    """Yields the mask cut into square tiles (so big woods give several small outlines)."""
    step = max(1, int(size_m / px))
    for r0 in range(0, mask.shape[0], step):
        for c0 in range(0, mask.shape[1], step):
            sub = np.zeros_like(mask)
            sub[r0:r0 + step, c0:c0 + step] = mask[r0:r0 + step, c0:c0 + step]
            if sub.any():
                yield sub


# ---------------------------------------------------------------------------
# Native grass
# ---------------------------------------------------------------------------

def native_mask(img, lab, px) -> tuple[np.ndarray, dict]:
    """
    Areas browner and drier than the course's own turf (red/green ratio above
    the fairways' 99th percentile, plant signal below their 5th), that aren't
    pavement, sand, trees or water, within 35 m of the course.
    """
    r, g, b, n = img
    sig = 2.0 / px
    rs, gs = ndi.gaussian_filter(r, sig), ndi.gaussian_filter(g, sig)
    ndvi = ndi.gaussian_filter((n - r) / (n + r + 1e-6) * 100, sig)
    bright = ndi.gaussian_filter((r + g + b) / 3, sig)
    rg = rs / (gs + 1e-6)
    turf = ndi.binary_erosion(lab == LABELS["turf"], iterations=5)
    if turf.sum() < 1000:
        return np.zeros(lab.shape, np.int32), {}
    rg_hi = float(np.percentile(rg[turf], 99))
    nd_lo = float(np.percentile(ndvi[turf], 5))
    mapped = ndi.binary_dilation(lab > 0, iterations=max(1, int(1.5 / px)))
    cand = (rg > rg_hi) & (ndvi < nd_lo + 2) & (ndvi > 4) & (bright < 175) & ~mapped
    cand = ndi.binary_fill_holes(ndi.binary_closing(ndi.binary_opening(cand, iterations=5), iterations=5))
    course = ndi.binary_dilation(np.isin(lab, (LABELS["turf"], LABELS["sand"])),
                                 iterations=int(NATIVE_FROM_COURSE_M / px))
    comp, k = ndi.label(cand & course)
    if k == 0:
        return comp, {"red_green_min": rg_hi, "plant_max": nd_lo + 2}
    sizes = ndi.sum(np.ones_like(comp, np.float32), comp, index=range(1, k + 1)) * px * px
    keep = [i + 1 for i in range(k) if sizes[i] >= MIN_NATIVE_M2]
    return np.where(np.isin(comp, keep), comp, 0), {"red_green_min": round(rg_hi, 3), "plant_max": round(nd_lo + 2, 1)}


# ---------------------------------------------------------------------------
# Outlines
# ---------------------------------------------------------------------------

def simplify(points: list, tol: float) -> list:
    """Ramer–Douglas–Peucker line simplification."""
    if len(points) < 3:
        return points
    (x0, y0), (x1, y1) = points[0], points[-1]
    dx, dy = x1 - x0, y1 - y0
    norm = math.hypot(dx, dy) or 1e-9
    dists = [abs(dy * (x - x0) - dx * (y - y0)) / norm for x, y in points[1:-1]]
    i = int(np.argmax(dists)) + 1
    if dists[i - 1] > tol:
        return simplify(points[:i + 1], tol)[:-1] + simplify(points[i:], tol)
    return [points[0], points[-1]]


def simplify_ring(ring: list, tol: float) -> list:
    """Simplifies a closed ring (first point == last) by splitting it at its farthest point."""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    if len(pts) < 4:
        return pts
    far = max(range(len(pts)), key=lambda i: math.dist(pts[0], pts[i]))
    a = simplify(pts[:far + 1], tol)
    b = simplify(pts[far:] + [pts[0]], tol)
    return a[:-1] + b[:-1]


def outlines(mask: np.ndarray, transform, tol: float = SIMPLIFY_M) -> list[list[tuple[float, float]]]:
    """Outer rings of a boolean mask, in the image CRS, simplified."""
    from rasterio.features import shapes
    rings = []
    for geom, val in shapes(mask.astype(np.uint8), mask=mask, transform=transform):
        if val != 1:
            continue
        ring = [tuple(p) for p in geom["coordinates"][0]]
        ring = simplify_ring(ring, tol)
        if len(ring) >= 3:
            rings.append(ring)
    return rings


def nearest_hole(ring, centres: dict) -> int:
    cx = sum(p[0] for p in ring) / len(ring)
    cy = sum(p[1] for p in ring) / len(ring)
    return min(centres, key=lambda h: min(math.dist((cx, cy), c) for c in centres[h]))


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def process(slug: str, write_extras: bool = False, overlays: bool = True) -> dict:
    load_course_config(slug)
    paths = get_paths(slug)
    if not paths["imagery"].exists():
        raise FileNotFoundError(f"No aerial image at {paths['imagery']}. Run 03_imagery.py first.")
    import rasterio
    with rasterio.open(paths["imagery"]) as ds:
        epsg = ds.crs.to_epsg()
    project = layout.make_projector(epsg)
    holes = layout.load_holes(paths["holes"])
    surfaces = layout.build_surfaces(holes, project)

    img, tf, crs = read_window(paths["imagery"], surfaces)
    px = pixel_size(tf)
    lab = label_raster(surfaces, tf, img.shape[1:])
    model = sand_model(img, lab)
    dist = sand_distance(img, model)
    areas, comp = sand_areas(img, lab, dist, px, DEFAULTS)
    lim = learn_limits(areas)

    from pyproj import Transformer
    to_ll = Transformer.from_crs(epsg, 4326, always_xy=True)
    centres = {}
    for s in surfaces:
        if s["surface"] in ("green", "fairway", "tee", "bunker"):
            centres.setdefault(s["hole"], []).append(layout.centroid(s["en"]))

    feats, per_hole = [], {}

    def add(ring, props):
        hole = nearest_hole(ring, centres)
        props = dict(props, hole=hole)
        per_hole.setdefault(hole, {}).setdefault(props["finding"], 0)
        per_hole[hole][props["finding"]] += 1
        ll = [list(to_ll.transform(x, y)) for x, y in ring]
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "Polygon", "coordinates": [ll + [ll[0]]]}})

    for a in areas:
        matched = a["arccos_overlap"] >= MATCH_OVERLAP
        if not matched and not is_bunker(a, lim):
            continue
        sl = a["slice"]
        sub = np.zeros(comp.shape, bool)
        sub[sl] = comp[sl] == a["id"]
        for ring in outlines(sub, tf):
            add(ring, {"finding": "bunker_matches_arccos" if matched else "bunker_missing_from_arccos",
                       "surface": "bunker", "area_m2": a["area_m2"], "source": "aerial photo"})

    # Arccos bunkers with no sand in the photo
    from rasterio.features import rasterize
    sandish = dist < lim["sand_distance_max"]
    bunkers = [s for s in surfaces if s["surface"] == "bunker"]
    ids = rasterize([({"type": "Polygon", "coordinates": [s["en"] + [s["en"][0]]]}, i + 1)
                     for i, s in enumerate(bunkers)], out_shape=lab.shape, transform=tf, fill=0, dtype="int32") \
        if bunkers else np.zeros(lab.shape, np.int32)
    if bunkers:
        with np.errstate(invalid="ignore"):  # bunkers outside the photo have no pixels
            share = ndi.mean(sandish, ids, index=range(1, len(bunkers) + 1))
        size = ndi.sum(np.ones_like(ids), ids, index=range(1, len(bunkers) + 1))
        reported = set()
        for i, s in enumerate(bunkers):
            key = layout.centroid(s["en"])
            key = (round(key[0]), round(key[1]))
            if size[i] and not np.isnan(share[i]) and share[i] < 0.10 and key not in reported:  # each shared bunker once
                reported.add(key)
                add(s["en"], {"finding": "arccos_bunker_no_sand_seen", "surface": "bunker",
                              "sand_share": round(float(share[i]), 2), "source": "Arccos"})

    trees, tree_info = tree_mask(img, lab, px)
    new_trees = trees & ~ndi.binary_dilation(lab == LABELS["trees"], iterations=max(1, int(2 / px)))
    for sub in tiles(new_trees, px, TREE_TILE_M):
        for ring in outlines(sub, tf, tol=1.0):
            area = abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]))) / 2
            if area >= MIN_TREE_M2:
                add(ring, {"finding": "trees_missing_from_arccos", "surface": "trees", "area_m2": round(area, 1),
                           "source": "aerial photo"})

    native, native_lim = native_mask(img, lab, px)
    for ring in outlines(native > 0, tf):
        area = abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]))) / 2
        if area >= MIN_NATIVE_M2:
            add(ring, {"finding": "native_grass", "surface": "native", "area_m2": round(area, 1),
                       "source": "aerial photo"})

    out = paths["out"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "photo_surfaces.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    counts = {}
    for f in feats:
        counts[f["properties"]["finding"]] = counts.get(f["properties"]["finding"], 0) + 1
    report = {"image": str(paths["imagery"].name), "pixel_m": px, "sand_model": {k: v for k, v in model.items() if k != "cov"},
              "bunker_limits": lim, "native_limits": native_lim, "trees": tree_info, "counts": counts,
              "per_hole": {str(h): v for h, v in sorted(per_hole.items())}}
    (out / "photo_check.json").write_text(json.dumps(report, indent=2))

    if write_extras:
        # Your own corrections stay; only earlier photo findings are replaced
        mine = []
        if paths["extras"].exists():
            mine = [f for f in json.loads(paths["extras"].read_text()).get("features") or []
                    if (f.get("properties") or {}).get("source") != "aerial photo"]
        keep = [f for f in feats if f["properties"]["finding"] in
                ("bunker_missing_from_arccos", "native_grass", "trees_missing_from_arccos")]
        paths["extras"].write_text(json.dumps({"type": "FeatureCollection", "features": mine + keep}, indent=1))
        report["extras_written"] = len(keep)
        report["extras_kept"] = len(mine)
    if overlays:
        draw_overlays(img, tf, surfaces, feats, epsg, out / "photo_check")
    return report


def draw_overlays(img, tf, surfaces, feats, epsg, out_dir: Path, max_px: int = 1400):
    """One photo per hole with Arccos outlines and the findings drawn on."""
    from PIL import Image, ImageDraw
    from pyproj import Transformer
    out_dir.mkdir(parents=True, exist_ok=True)
    to_en = Transformer.from_crs(4326, epsg, always_xy=True)
    inv = ~tf
    rgb = np.clip(np.moveaxis(img[:3], 0, -1), 0, 255).astype(np.uint8)
    colours = {"bunker_matches_arccos": (0, 220, 0), "bunker_missing_from_arccos": (255, 30, 30),
               "arccos_bunker_no_sand_seen": (255, 140, 0), "native_grass": (170, 60, 240),
               "trees_missing_from_arccos": (0, 90, 255)}
    edge = {"green": (40, 120, 255), "fairway": (255, 255, 255), "tee": (255, 0, 255), "bunker": (255, 230, 0)}
    for hole in sorted({s["hole"] for s in surfaces}):
        mine = [s for s in surfaces if s["hole"] == hole and s["surface"] in edge]
        if not mine:
            continue
        cols = [inv @ p for s in mine for p in s["en"]]
        c0, c1 = int(min(c for c, _ in cols)) - 80, int(max(c for c, _ in cols)) + 80
        r0, r1 = int(min(r for _, r in cols)) - 80, int(max(r for _, r in cols)) + 80
        c0, r0 = max(c0, 0), max(r0, 0)
        c1, r1 = min(c1, rgb.shape[1]), min(r1, rgb.shape[0])
        im = Image.fromarray(rgb[r0:r1, c0:c1]).convert("RGBA")
        layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        to_px = lambda e, n: ((inv @ (e, n))[0] - c0, (inv @ (e, n))[1] - r0)  # noqa: E731
        for f in feats:
            ring = [to_px(*to_en.transform(x, y)) for x, y in f["geometry"]["coordinates"][0]]
            d.polygon(ring, fill=colours[f["properties"]["finding"]] + (120,))
        for s in mine:
            ring = [to_px(e, n) for e, n in s["en"]]
            d.line(ring + [ring[0]], fill=edge[s["surface"]] + (255,), width=3)
        im = Image.alpha_composite(im, layer).convert("RGB")
        k = max_px / max(im.size)
        if k < 1:
            im = im.resize((int(im.size[0] * k), int(im.size[1] * k)))
        im.save(out_dir / f"hole_{hole:02d}.jpg", quality=85)


def main():
    parser = argparse.ArgumentParser(description="Find bunkers and native grass in the aerial photo")
    parser.add_argument("--course", default="tedesco")
    parser.add_argument("--write-extras", action="store_true",
                        help="seed courses/<slug>/extra_surfaces.geojson with new bunkers and native areas")
    parser.add_argument("--no-overlays", action="store_true")
    args = parser.parse_args()
    print(f"Photo check for {args.course}")
    r = process(args.course, args.write_extras, not args.no_overlays)
    print(f"  Sand learned from {r['sand_model']['pixels']} bunker pixels; "
          f"bunker limits learned from {r['bunker_limits']['learned_from']} matching bunkers")
    for k, v in sorted(r["counts"].items()):
        print(f"  {k}: {v}")
    if "extras_written" in r:
        print(f"  Wrote {r['extras_written']} photo shapes to extra_surfaces.geojson (kept {r['extras_kept']} of yours); "
              f"review them in QGIS")


if __name__ == "__main__":
    main()
