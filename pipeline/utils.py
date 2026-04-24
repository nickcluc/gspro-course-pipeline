"""
utils.py — Shared utilities for the GSPro course pipeline.
"""

import json
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_course_config(slug: str) -> dict:
    """Loads courses/<slug>/course.json."""
    with open(REPO_ROOT / "courses" / slug / "course.json") as f:
        return json.load(f)


def bbox_from_center(lat: float, lon: float, buffer_km: float) -> dict:
    """
    Returns a WGS84 bounding box dict given a center point and buffer in km.
    Uses per-latitude corrected longitude degrees.
    """
    lat_deg = buffer_km / 110.574
    lon_deg = buffer_km / (111.320 * math.cos(math.radians(lat)))
    return {
        "xmin": lon - lon_deg,
        "ymin": lat - lat_deg,
        "xmax": lon + lon_deg,
        "ymax": lat + lat_deg,
    }


def bbox_to_string(bbox: dict) -> str:
    return f"{bbox['xmin']},{bbox['ymin']},{bbox['xmax']},{bbox['ymax']}"
