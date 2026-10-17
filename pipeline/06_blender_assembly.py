"""
06_blender_assembly.py — Blender scene assembly

Builds the whole course as one Blender scene, from earlier stages' outputs:

  1. Terrain mesh (Stage 04 OBJ)
  2. Every Arccos surface (green, fairway, tee, bunker, water, trees) draped
     onto the terrain, one object per shape, named h07_green, h12_bunker_03, ...
  3. PuttView green meshes (Stage 04), each turned and moved to best fit the
     Arccos outline of that green, and set to the terrain's height
  4. Markers for every tee and pin spot (Stage 05 holes.json)
  5. A camera per hole (behind the tee, looking at the green), and optional
     QA renders: a tee view and a top view of every hole

The work is split in two: `plan_scene()` is plain Python (numpy only) and
works out every vertex, name and position; `build_scene()` only turns that
plan into Blender objects. The plan can be tested without Blender.

Coordinates in Blender: X = east, Y = north (metres from the terrain centre,
as in Stage 04), Z = elevation in metres above sea level.

Usage (run inside Blender):
    blender --background --python pipeline/06_blender_assembly.py -- --course tedesco
    blender --background --python pipeline/06_blender_assembly.py -- --course tedesco --render

Input:
    courses/<slug>/output/terrain/<slug>_terrain.obj        (Stage 04)
    courses/<slug>/output/greens/hole_XX_green.obj          (Stage 04, optional)
    courses/<slug>/data/processed/grids/hole_XX_green.json  (Stage 02, optional: better green fit)
    courses/<slug>/output/layout/holes.json                 (Stage 05)
    courses/<slug>/output/layout/surfaces_local.json        (Stage 05)

Optional config (courses/<slug>/course.json):
    "blender": {"green_rotation_deg": {"7": 90}}   fixes a green's turn by hand

Output:
    courses/<slug>/output/blender/<slug>.blend
    courses/<slug>/output/blender/assembly_report.json
    courses/<slug>/output/blender/renders/hole_XX_tee.png, hole_XX_top.png   (--render)
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import REPO_ROOT, load_course_config  # noqa: E402

SURFACE_LIFT_M = 0.03     # surfaces sit just above the terrain so they show in renders
# Each surface type sits a little higher than the one under it, so overlaps don't flicker
LAYER_STEP_M = 0.01
LAYER_ORDER = ("fairway", "tee", "trees", "green", "bunker", "water")
CAMERA_BACK_M = 40.0      # camera distance behind the tee
CAMERA_UP_M = 25.0        # camera height above the tee (a flyover-style view)

# Display colours for QA (the OPCD scheme used in Stage 05's SVG)
COLOURS = {
    "terrain": "#278438", "fairway": "#43E561", "tee": "#A0E5B8", "trees": "#164B20",
    "green": "#BCE5A4", "bunker": "#E5E5AA", "water": "#0000C0", "green_puttview": "#7FD67F",
    "tee_marker": "#FFFFFF", "pin_marker": "#E03C31",
}
MARKER_RADIUS_M = {"tee": 2.0, "pin": 1.5}  # discs drawn so markers show in renders
SURFACES = LAYER_ORDER


def lift(surface: str, nth: int = 0) -> float:
    """
    Height above the terrain for a surface type. `nth` (the shape's number within
    its type) adds a few millimetres, so two overlapping shapes of one type
    (a pond drawn twice, touching tree areas) don't flicker in renders.
    """
    return SURFACE_LIFT_M + LAYER_STEP_M * LAYER_ORDER.index(surface) + 0.0015 * (nth % 5)


def shape_key(points: list) -> tuple:
    """Identifies a shape regardless of start point, so copies on two holes' maps are found."""
    pts = sorted((round(x, 1), round(y, 1)) for x, y in points)
    return tuple(pts)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def get_paths(slug: str) -> dict:
    out = REPO_ROOT / "courses" / slug / "output"
    return {
        "terrain": out / "terrain" / f"{slug}_terrain.obj",
        "greens": out / "greens",
        "grids": REPO_ROOT / "courses" / slug / "data" / "processed" / "grids",
        "holes": out / "layout" / "holes.json",
        "surfaces": out / "layout" / "surfaces_local.json",
        "out": out / "blender",
    }


# ---------------------------------------------------------------------------
# OBJ reading and terrain height
# ---------------------------------------------------------------------------

def read_obj(path: Path) -> tuple[np.ndarray, list[list[int]]]:
    """Vertices (n x 3, as written) and faces (0-based) of a Wavefront OBJ."""
    verts, faces = [], []
    with open(path) as f:
        for line in f:
            if line.startswith("v "):
                verts.append([float(v) for v in line.split()[1:4]])
            elif line.startswith("f "):
                faces.append([int(p.split("/")[0]) - 1 for p in line.split()[1:]])
    return np.array(verts, dtype=np.float64), faces


class TerrainSampler:
    """
    Bilinear terrain height from Stage 04's terrain OBJ, which is a regular
    grid with OBJ x = east, y = elevation, z = north (metres from centre).
    """

    def __init__(self, obj_verts: np.ndarray):
        xs = np.unique(np.round(obj_verts[:, 0], 3))
        ns = np.unique(np.round(obj_verts[:, 2], 3))
        grid = np.full((len(ns), len(xs)), np.nan)
        xi = np.searchsorted(xs, np.round(obj_verts[:, 0], 3))
        ni = np.searchsorted(ns, np.round(obj_verts[:, 2], 3))
        grid[ni, xi] = obj_verts[:, 1]
        self.xs, self.ns, self.grid = xs, ns, grid

    def height(self, x: float, n: float) -> float:
        xs, ns, g = self.xs, self.ns, self.grid
        x = min(max(x, xs[0]), xs[-1])
        n = min(max(n, ns[0]), ns[-1])
        i = min(max(int(np.searchsorted(xs, x)) - 1, 0), len(xs) - 2)
        j = min(max(int(np.searchsorted(ns, n)) - 1, 0), len(ns) - 2)
        tx = (x - xs[i]) / (xs[i + 1] - xs[i])
        tn = (n - ns[j]) / (ns[j + 1] - ns[j])
        h00, h10, h01, h11 = g[j, i], g[j, i + 1], g[j + 1, i], g[j + 1, i + 1]
        return float((h00 * (1 - tx) + h10 * tx) * (1 - tn) + (h01 * (1 - tx) + h11 * tx) * tn)


def terrain_mesh(obj_verts: np.ndarray, faces: list[list[int]]) -> dict:
    """Terrain in Blender axes: (east, north, elevation)."""
    v = obj_verts[:, [0, 2, 1]]
    return {"verts": v.tolist(), "faces": faces}


# ---------------------------------------------------------------------------
# Polygons
# ---------------------------------------------------------------------------

def densify(points: list[list[float]], max_edge: float) -> list[tuple[float, float]]:
    """Adds points along each edge so no edge is longer than max_edge."""
    out = []
    n = len(points)
    for k in range(n):
        (x0, y0), (x1, y1) = points[k], points[(k + 1) % n]
        steps = max(1, math.ceil(math.dist((x0, y0), (x1, y1)) / max_edge))
        out += [(x0 + (x1 - x0) * s / steps, y0 + (y1 - y0) * s / steps) for s in range(steps)]
    return out


def inside(x: float, y: float, poly: list) -> bool:
    hit = False
    j = len(poly) - 1
    for i in range(len(poly)):
        yi, yj = poly[i][1], poly[j][1]
        if (yi > y) != (yj > y):
            xi = poly[i][0] + (y - yi) * (poly[j][0] - poly[i][0]) / (yj - yi)
            if x < xi:
                hit = not hit
        j = i
    return hit


def polygon_centre(poly: list) -> tuple[float, float]:
    a = cx = cy = 0.0
    for i in range(len(poly)):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % len(poly)]
        c = x0 * y1 - x1 * y0
        a, cx, cy = a + c, cx + (x0 + x1) * c, cy + (y0 + y1) * c
    if abs(a) < 1e-9:
        return float(np.mean([p[0] for p in poly])), float(np.mean([p[1] for p in poly]))
    return cx / (3 * a), cy / (3 * a)


# ---------------------------------------------------------------------------
# Greens
# ---------------------------------------------------------------------------

def green_footprint(grid_json: Path | None, obj_verts: np.ndarray) -> np.ndarray:
    """
    The green's shape in metres, in the green OBJ's (x, grid y) frame. Uses the
    Stage 02 PuttView grid points when there (they cover only the green); the
    Stage 04 mesh is filled out to a rectangle, so it's the fallback.
    """
    if grid_json and grid_json.exists():
        pts = json.loads(grid_json.read_text()).get("points") or []
        if pts:
            yd = 0.9144
            return np.array([[p["x"] * yd, p["y"] * yd] for p in pts])
    return np.column_stack([obj_verts[:, 0], -obj_verts[:, 2]])


def fit_green(footprint: np.ndarray, outline: list, step_deg: float = 5.0, max_points: int = 400) -> dict:
    """
    Turns a PuttView green's footprint (n x 2, metres) about its centre, puts
    that centre on the Arccos green's centre, and picks the turn that puts the
    most footprint points inside the Arccos outline.

    Returns the turn (degrees, counter-clockwise), the share of points inside
    at that turn (`fit`), the pivot (footprint centre) and the target centre.
    """
    pivot = footprint.mean(axis=0)
    local = footprint - pivot
    if len(local) > max_points:
        local = local[np.linspace(0, len(local) - 1, max_points).astype(int)]
    target = np.array(polygon_centre(outline))
    best = {"rotation_deg": 0.0, "fit": -1.0}
    for deg in np.arange(0, 360, step_deg):
        pts = _turn(local, deg) + target
        fit = sum(inside(x, y, outline) for x, y in pts) / len(pts)
        if fit > best["fit"] + 1e-9:
            best = {"rotation_deg": float(deg), "fit": round(fit, 3)}
    best.update(pivot=pivot.tolist(), centre=target.tolist())
    return best


def _turn(uv: np.ndarray, deg: float) -> np.ndarray:
    """Counter-clockwise turn of row vectors by deg."""
    t = math.radians(deg)
    return uv @ np.array([[math.cos(t), math.sin(t)], [-math.sin(t), math.cos(t)]])


def place_green(obj_verts: np.ndarray, rotation_deg: float, pivot: tuple[float, float],
                centre: tuple[float, float], base_height: float) -> np.ndarray:
    """
    Stage 04 green OBJ (x, relative height, z = -grid y) -> Blender (east, north, elevation):
    turned by rotation_deg about `pivot`, moved so the pivot lands on `centre`, with the
    green's mean height set to base_height.
    """
    uv = np.column_stack([obj_verts[:, 0], -obj_verts[:, 2]]) - np.array(pivot)
    uv = _turn(uv, rotation_deg) + np.array(centre)
    z = obj_verts[:, 1] - obj_verts[:, 1].mean() + base_height
    return np.column_stack([uv, z])


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def plan_scene(slug: str, max_edge_m: float = 3.0) -> dict:
    """Everything build_scene() needs, as plain data."""
    cfg = load_course_config(slug)
    paths = get_paths(slug)
    for key in ("terrain", "holes", "surfaces"):
        if not paths[key].exists():
            raise FileNotFoundError(f"Missing {paths[key]}. Run stages 04 and 05 first.")

    tv, tf = read_obj(paths["terrain"])
    sampler = TerrainSampler(tv)
    holes = json.loads(paths["holes"].read_text())["holes"]
    surfaces = json.loads(paths["surfaces"].read_text())["surfaces"]
    turns = {int(k): v for k, v in (cfg.get("blender", {}).get("green_rotation_deg") or {}).items()}

    plan = {"terrain": terrain_mesh(tv, tf), "surfaces": [], "greens": [], "markers": [],
            "cameras": [], "report": {"holes": {}}}

    # Surfaces: densified outline, draped (Blender fills and subdivides, then re-drapes)
    counts: dict[tuple[int, str], int] = {}
    totals: dict[tuple[int, str], int] = {}
    _seen: set = set()
    for t in surfaces:
        if t["surface"] in SURFACES and (t["surface"], shape_key(t["points"])) not in _seen:
            _seen.add((t["surface"], shape_key(t["points"])))
            totals[(t["hole"], t["surface"])] = totals.get((t["hole"], t["surface"]), 0) + 1
    outlines: dict[int, list] = {}
    seen: dict = {}
    duplicates = 0
    for s in surfaces:
        if s["surface"] not in SURFACES:
            continue
        k = (s["surface"], shape_key(s["points"]))
        if k in seen:  # same shape on another hole's map (a shared pond, a tree line): build once, show on both
            duplicates += 1
            if s["hole"] not in seen[k]["also_holes"] and s["hole"] != seen[k]["hole"]:
                seen[k]["also_holes"].append(s["hole"])
            continue
        key = (s["hole"], s["surface"])
        counts[key] = counts.get(key, 0) + 1
        name = f"h{s['hole']:02d}_{s['surface']}"
        if totals[key] > 1:
            name += f"_{counts[key]:02d}"
        ring = densify(s["points"], max_edge_m)
        nth = sum(1 for p in plan["surfaces"] if p["surface"] == s["surface"])
        h = lift(s["surface"], nth)
        spec = {
            "name": name, "hole": s["hole"], "also_holes": [], "surface": s["surface"], "lift_m": h,
            "outline": [(x, y, sampler.height(x, y) + h) for x, y in ring],
        }
        seen[k] = spec
        plan["surfaces"].append(spec)
        if s["surface"] == "green":
            outlines.setdefault(s["hole"], []).append(s["points"])

    # PuttView greens, fitted to the Arccos outline
    for h in holes:
        n = h["hole"]
        obj = paths["greens"] / f"hole_{n:02d}_green.obj"
        rep = {"puttview_green": obj.exists()}
        if obj.exists() and outlines.get(n):
            gv, gf = read_obj(obj)
            outline = max(outlines[n], key=len)
            grid = paths["grids"] / f"hole_{n:02d}_green.json"
            fit = fit_green(green_footprint(grid, gv), outline)
            rotation = float(turns.get(n, fit["rotation_deg"]))
            base = float(np.mean([sampler.height(x, y) for x, y in outline]))
            verts = place_green(gv, rotation, fit["pivot"], fit["centre"], base)
            plan["greens"].append({"name": f"h{n:02d}_green_puttview", "hole": n,
                                   "verts": verts.tolist(), "faces": gf})
            rep.update(rotation_deg=rotation, rotation_set_by="course.json" if n in turns else "best fit",
                       fit=fit["fit"])
        plan["report"]["holes"][n] = rep

        # Markers and camera
        tee_spots = []
        for t in h.get("tees") or []:
            p = t.get("position")
            if p:
                z = sampler.height(p["x"], p["z"])
                plan["markers"].append({"name": f"h{n:02d}_tee_{(t.get('name') or t.get('tee_id'))}",
                                        "kind": "tee", "hole": n, "at": (p["x"], p["z"], z)})
                tee_spots.append((p["x"], p["z"], z))
        for i, p in enumerate(h.get("pins") or [], 1):
            plan["markers"].append({"name": f"h{n:02d}_pin_{i}", "kind": "pin", "hole": n,
                                    "at": (p["x"], p["z"], sampler.height(p["x"], p["z"]))})
        g = h.get("green")
        if g and tee_spots:
            back = max(tee_spots, key=lambda t: math.dist(t[:2], (g["x"], g["z"])))  # back tee
            gz = sampler.height(g["x"], g["z"])
            d = math.dist(back[:2], (g["x"], g["z"])) or 1.0
            ux, uy = (back[0] - g["x"]) / d, (back[1] - g["z"]) / d
            plan["cameras"].append({
                "name": f"h{n:02d}_camera", "hole": n,
                "at": (back[0] + ux * CAMERA_BACK_M, back[1] + uy * CAMERA_BACK_M, back[2] + CAMERA_UP_M),
                "look_at": (g["x"], g["z"], gz),
                "top_centre": ((back[0] + g["x"]) / 2, (back[1] + g["z"]) / 2, max(back[2], gz)),
                "top_span_m": d * 1.25,
                # turn the top view so the hole runs up the image, tee at the bottom
                "top_turn_rad": math.atan2(g["z"] - back[1], g["x"] - back[0]) - math.pi / 2,
            })
    plan["report"]["duplicate_shapes_skipped"] = duplicates
    plan["report"]["counts"] = {"surfaces": len(plan["surfaces"]), "puttview_greens": len(plan["greens"]),
                                "markers": len(plan["markers"]), "cameras": len(plan["cameras"])}
    plan["max_edge_m"] = max_edge_m
    return plan


# ---------------------------------------------------------------------------
# Blender (bpy) — only this part needs Blender
# ---------------------------------------------------------------------------

def _linear(c: float) -> float:
    """sRGB (screen) colour value -> the linear value Blender stores."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _material(bpy, name: str, hex_colour: str):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    r, g, b = (_linear(int(hex_colour[i:i + 2], 16) / 255) for i in (1, 3, 5))
    m.diffuse_color = (r, g, b, 1.0)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.9
    return m


def _mesh_object(bpy, name, verts, faces, collection, material=None):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if material:
        obj.data.materials.append(material)
    return obj


def _surface_object(bpy, bmesh, spec, sampler_fn, max_edge, collection, material):
    height = spec["lift_m"]
    """Fills the draped outline, subdivides long edges, and re-drapes every vertex."""
    bm = bmesh.new()
    vs = [bm.verts.new(p) for p in spec["outline"]]
    try:
        bm.faces.new(vs)
    except ValueError:
        bm.free()
        return None
    bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method="BEAUTY", ngon_method="BEAUTY")
    for _ in range(6):
        long_edges = [e for e in bm.edges if e.calc_length() > max_edge]
        if not long_edges:
            break
        bmesh.ops.subdivide_edges(bm, edges=long_edges, cuts=1, use_grid_fill=False)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
    for v in bm.verts:
        v.co.z = sampler_fn(v.co.x, v.co.y) + height
    mesh = bpy.data.meshes.new(spec["name"])
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(spec["name"], mesh)
    collection.objects.link(obj)
    obj.data.materials.append(material)
    return obj


def build_scene(slug: str, plan: dict, render: bool = False, engine: str = "workbench",
                views: tuple = ("tee", "top")) -> Path:
    import bpy  # first: bmesh and mathutils load with it
    import bmesh
    from mathutils import Vector

    paths = get_paths(slug)
    paths["out"].mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"

    tv, _ = read_obj(paths["terrain"])
    sampler = TerrainSampler(tv)
    mats = {k: _material(bpy, k, c) for k, c in COLOURS.items()}

    root = scene.collection
    terrain_col = bpy.data.collections.new("Terrain")
    root.children.link(terrain_col)
    _mesh_object(bpy, "terrain", plan["terrain"]["verts"], plan["terrain"]["faces"], terrain_col, mats["terrain"])

    hole_cols = {}

    def hole_col(n):
        if n not in hole_cols:
            hole_cols[n] = bpy.data.collections.new(f"Hole {n:02d}")
            root.children.link(hole_cols[n])
        return hole_cols[n]

    failed = []
    for spec in plan["surfaces"]:
        obj = _surface_object(bpy, bmesh, spec, sampler.height, plan["max_edge_m"],
                              hole_col(spec["hole"]), mats[spec["surface"]])
        if not obj:
            failed.append(spec["name"])
            continue
        for n in spec.get("also_holes") or []:  # shared shapes also belong to the other holes' collections
            hole_col(n).objects.link(obj)
    for g in plan["greens"]:
        _mesh_object(bpy, g["name"], g["verts"], g["faces"], hole_col(g["hole"]), mats["green_puttview"])
    drawn: list = []
    for m in plan["markers"]:
        r = MARKER_RADIUS_M[m["kind"]]
        x, y, z = m["at"]
        # several tees often share one box: draw one disc per spot
        if any(k == m["kind"] and math.dist((x, y), p) < 0.5 for k, p in drawn):
            continue
        drawn.append((m["kind"], (x, y)))
        ring = [(x + r * math.cos(a), y + r * math.sin(a), z + 0.2) for a in np.linspace(0, 2 * math.pi, 16, endpoint=False)]
        _mesh_object(bpy, m["name"], ring, [list(range(16))], hole_col(m["hole"]), mats[f"{m['kind']}_marker"])

    renders = paths["out"] / "renders"
    if engine == "cycles":  # CPU-only; for machines without a GPU / OpenGL
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 16
        world = bpy.data.worlds.new("sky")
        world.color = (0.6, 0.7, 0.85)
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        if bg:
            bg.inputs["Color"].default_value = (0.6, 0.7, 0.85, 1.0)
        scene.world = world
        sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
        sun.rotation_euler = (0.6, 0.2, 0.8)
        root.objects.link(sun)
    else:
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.display.shading.color_type = "MATERIAL"
    scene.render.resolution_x, scene.render.resolution_y = 1600, 900
    for c in plan["cameras"]:
        cam = bpy.data.objects.new(c["name"], bpy.data.cameras.new(c["name"]))
        cam.location = c["at"]
        cam.rotation_euler = (Vector(c["look_at"]) - Vector(c["at"])).to_track_quat("-Z", "Y").to_euler()
        cam.data.clip_end = 5000
        hole_col(c["hole"]).objects.link(cam)
        if render:
            # Renders show only this hole's shapes on the terrain, so neighbouring holes don't crowd them
            for n, col in hole_cols.items():
                col.hide_render = n != c["hole"]
        if render and "tee" in views:
            renders.mkdir(parents=True, exist_ok=True)
            scene.camera = cam
            scene.render.filepath = str(renders / f"hole_{c['hole']:02d}_tee.png")
            bpy.ops.render.render(write_still=True)
        if render and "top" in views:
            renders.mkdir(parents=True, exist_ok=True)
            top = bpy.data.objects.new(f"{c['name']}_top", bpy.data.cameras.new(f"{c['name']}_top"))
            top.data.type = "ORTHO"
            top.data.ortho_scale = c["top_span_m"]  # covers the tall side of a portrait image
            top.data.clip_end = 5000
            top.location = (c["top_centre"][0], c["top_centre"][1], c["top_centre"][2] + 500)
            top.rotation_euler = (0, 0, c["top_turn_rad"])
            hole_col(c["hole"]).objects.link(top)
            scene.camera = top
            scene.render.resolution_x, scene.render.resolution_y = 900, 1600
            scene.render.filepath = str(renders / f"hole_{c['hole']:02d}_top.png")
            bpy.ops.render.render(write_still=True)
            scene.render.resolution_x, scene.render.resolution_y = 1600, 900
    for col in hole_cols.values():
        col.hide_render = False

    report = dict(plan["report"], failed_surfaces=failed)
    (paths["out"] / "assembly_report.json").write_text(json.dumps(report, indent=2))
    blend = paths["out"] / f"{slug}.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    return blend


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser(description="Assemble the course in Blender")
    parser.add_argument("--course", default="tedesco")
    parser.add_argument("--max-edge", type=float, default=3.0, help="longest surface edge in metres")
    parser.add_argument("--render", action="store_true", help="render a tee view and top view of each hole")
    parser.add_argument("--views", default="tee,top", help="which renders: tee, top, or tee,top")
    parser.add_argument("--engine", choices=["workbench", "cycles"], default="workbench",
                        help="workbench is fast (needs a GPU); cycles works on CPU-only machines")
    parser.add_argument("--plan-only", action="store_true", help="check inputs and print the plan (no Blender)")
    args = parser.parse_args(argv)

    print(f"Blender assembly for {args.course}")
    plan = plan_scene(args.course, args.max_edge)
    print(f"  Plan: {plan['report']['counts']}")
    for n, r in sorted(plan["report"]["holes"].items()):
        if r.get("fit") is not None and r["fit"] < 0.8:
            print(f"  Hole {n}: PuttView green fits the Arccos outline only {r['fit']:.0%}; "
                  f"check it and set blender.green_rotation_deg in course.json if needed")
    if args.plan_only:
        return
    try:
        import bpy  # noqa: F401
    except ImportError:
        print("ERROR: run this inside Blender: blender --background --python pipeline/06_blender_assembly.py -- --course <slug>")
        sys.exit(1)
    blend = build_scene(args.course, plan, args.render, args.engine, tuple(args.views.split(",")))
    print(f"  Saved {blend}")


if __name__ == "__main__":
    main()
