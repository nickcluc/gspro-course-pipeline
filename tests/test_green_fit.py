"""
Tests for pipeline/green_fit.py (placing a PuttView green outline on the map)

Run with:
    .venv/bin/pytest tests/test_green_fit.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import green_fit as gf  # noqa: E402

# A kidney-ish green that isn't symmetric, in book metres
BOOK = np.array([(0, 0), (20, 0), (26, 8), (22, 18), (12, 14), (4, 20), (-4, 10)], float)


def on_map(points, deg, mirrored, centre):
    fit = {"rotation_deg": deg, "mirrored": mirrored, "pivot": BOOK.mean(0).tolist(), "centre": centre}
    return gf.place(points, fit)


def test_inside_many_square():
    sq = [(0, 0), (4, 0), (4, 4), (0, 4)]
    assert gf.inside_many(np.array([[2, 2], [5, 2], [-1, -1]]), sq).tolist() == [True, False, False]


@pytest.mark.parametrize("mirrored", [False, True])
def test_finds_turn_flip_and_place(mirrored):
    target = on_map(BOOK, 117, mirrored, (344100.0, 4705200.0))
    fit = gf.fit_outline(BOOK, target)
    assert fit["mirrored"] == mirrored
    assert fit["iou"] > 0.93
    placed = gf.place(BOOK, fit)
    assert np.abs(placed - target).max() < 1.5


def test_bigger_book_green_keeps_its_own_size():
    # The map's green is drawn small (as Arccos does); the book's size is kept
    small = BOOK.mean(0) + (BOOK - BOOK.mean(0)) * 0.7
    fit = gf.fit_outline(BOOK, on_map(small, 40, False, (0.0, 0.0)))
    assert gf.area(gf.place(BOOK, fit)) == pytest.approx(gf.area(BOOK))
    assert fit["rotation_deg"] == pytest.approx(40, abs=4)
