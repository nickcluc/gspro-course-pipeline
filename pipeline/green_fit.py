"""
Fits a PuttView green outline onto the course map.

PuttView pages aren't printed north-up and may be flipped relative to the map, so the
book outline is tried at every turn, both ways round, and nudged a few metres, and the
placement that overlaps the map's green the most is kept. The map's green (the aerial
photo outline, or Arccos's) only places the book green; the shape and size come from
the book.

Book coordinates: metres, x to the right on the page, y as Stage 02 writes it
(the grid's y). Placing a point: subtract the pivot, flip y if `mirrored`, turn
counter-clockwise by `rotation_deg`, add `centre`.
"""

import math

import numpy as np

YARD_M = 0.9144


def inside_many(pts: np.ndarray, poly) -> np.ndarray:
    """Point-in-polygon for many points at once (even-odd rule)."""
    poly = np.asarray(poly, float)
    x, y = pts[:, 0], pts[:, 1]
    out = np.zeros(len(pts), bool)
    x0, y0 = poly[:, 0], poly[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    for a, b, c, d in zip(x0, y0, x1, y1):
        if b == d:
            continue
        crosses = (b > y) != (d > y)
        xc = a + (y - b) * (c - a) / (d - b)
        out ^= crosses & (x < xc)
    return out


def area(poly) -> float:
    p = np.asarray(poly, float)
    return abs(float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(np.roll(p[:, 0], -1), p[:, 1]))) / 2


def _turn(uv: np.ndarray, deg: float) -> np.ndarray:
    t = math.radians(deg)
    return uv @ np.array([[math.cos(t), math.sin(t)], [-math.sin(t), math.cos(t)]])


def place(points, fit: dict) -> np.ndarray:
    """Book points (n x 2, metres) -> map points, using a fit from fit_outline()."""
    uv = np.asarray(points, float) - np.array(fit["pivot"])
    if fit.get("mirrored"):
        uv = uv * np.array([1.0, -1.0])
    return _turn(uv, fit["rotation_deg"]) + np.array(fit["centre"])


def fit_outline(book, target, res: float = 0.5, shift_m: float = 3.0) -> dict:
    """
    Best placement of a book outline (metres) on a map outline (metres).
    Returns rotation_deg, mirrored, pivot, centre and iou (overlap of the two shapes,
    0 to 1), plus both areas.
    """
    book = np.asarray(book, float)
    target = np.asarray(target, float)
    pivot = book.mean(axis=0)
    centre0 = target.mean(axis=0)
    reach = float(np.max(np.hypot(*(book - pivot).T))) + shift_m + 1
    lo = np.minimum(target.min(0), centre0 - reach)
    hi = np.maximum(target.max(0), centre0 + reach)
    gx, gy = np.meshgrid(np.arange(lo[0], hi[0], res) + res / 2, np.arange(lo[1], hi[1], res) + res / 2)
    grid = np.column_stack([gx.ravel(), gy.ravel()])
    in_target = inside_many(grid, target)

    # The book shape as a fine mask in its own frame, for fast lookups
    fine = res / 2
    blo, bhi = book.min(0) - fine, book.max(0) + fine
    bx, by = np.meshgrid(np.arange(blo[0], bhi[0], fine) + fine / 2, np.arange(blo[1], bhi[1], fine) + fine / 2)
    bmask = inside_many(np.column_stack([bx.ravel(), by.ravel()]), book).reshape(bx.shape)

    def score(deg, mirrored, centre):
        q = _turn(grid - centre, -deg)  # back into the book's frame
        if mirrored:
            q = q * np.array([1.0, -1.0])
        q = q + pivot
        ix = np.floor((q[:, 0] - blo[0]) / fine).astype(int)
        iy = np.floor((q[:, 1] - blo[1]) / fine).astype(int)
        ok = (ix >= 0) & (iy >= 0) & (ix < bmask.shape[1]) & (iy < bmask.shape[0])
        in_book = np.zeros(len(grid), bool)
        in_book[ok] = bmask[iy[ok], ix[ok]]
        union = (in_book | in_target).sum()
        return (in_book & in_target).sum() / union if union else 0.0

    best = None
    for mirrored in (False, True):
        coarse = max(((score(d, mirrored, centre0), d) for d in np.arange(0, 360, 3.0)))
        top = (coarse[0], coarse[1], centre0)
        for d in np.arange(coarse[1] - 3, coarse[1] + 3.5, 1.0):
            for dx in np.arange(-shift_m, shift_m + 0.01, 0.5):
                for dy in np.arange(-shift_m, shift_m + 0.01, 0.5):
                    c = centre0 + (dx, dy)
                    s = score(d, mirrored, c)
                    if s > top[0]:
                        top = (s, d, c)
        if best is None or top[0] > best[0]:
            best = (top[0], top[1] % 360, top[2], mirrored)
    iou, deg, centre, mirrored = best
    return {"rotation_deg": float(deg), "mirrored": bool(mirrored), "pivot": pivot.tolist(),
            "centre": [float(centre[0]), float(centre[1])], "iou": round(float(iou), 3),
            "book_m2": round(area(book), 1), "map_m2": round(area(target), 1)}
