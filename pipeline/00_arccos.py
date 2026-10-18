"""
00_arccos.py — Arccos course map and round download

Downloads a course's hole maps from Arccos (par, stroke index, tee yardages,
surface outlines, elevation strip) and, optionally, your own rounds at that
course (shot GPS and pin positions). Stage 05 turns these into the course
layout.

The Arccos map data belongs to Arccos. Use it for a personal build only; don't
publish a course built from it.

Usage:
    python pipeline/00_arccos.py --course tedesco
    python pipeline/00_arccos.py --course tedesco --rounds

Config (courses/<slug>/course.json):
    "arccos": {"course_id": 6878, "course_version": 4}

Auth (your own Arccos account):
    1. Open dashboard.arccosgolf.com, choose "Send Login Link", and open the
       emailed link in a desktop browser.
    2. Copy the cookies `access_token` and `user_id` from the browser's dev tools.
    3. Either set ARCCOS_ACCESS_TOKEN and ARCCOS_USER_ID, or save
       {"access_token": "...", "user_id": "..."} as
       courses/<slug>/data/raw/arccos/auth.json (raw data is git-ignored).
    Tokens expire after a while; a 401 error means get a fresh one.

Output:
    courses/<slug>/data/raw/arccos/holes/hole_XX.json
    courses/<slug>/data/raw/arccos/rounds/<roundId>.json   (with --rounds)

Downloads are cached: files that already exist are skipped.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests

from utils import REPO_ROOT, load_course_config

API = "https://api.arccosgolf.com"
REQUEST_GAP_S = 0.3  # pause between calls, to be polite to Arccos


def get_paths(slug: str) -> dict:
    raw = REPO_ROOT / "courses" / slug / "data" / "raw" / "arccos"
    return {"raw": raw, "holes": raw / "holes", "rounds": raw / "rounds", "auth": raw / "auth.json"}


def load_auth(auth_path: Path) -> tuple[str, str]:
    """Returns (access_token, user_id) from env vars or auth.json."""
    token = os.environ.get("ARCCOS_ACCESS_TOKEN")
    uid = os.environ.get("ARCCOS_USER_ID")
    if not (token and uid) and auth_path.exists():
        data = json.loads(auth_path.read_text())
        token, uid = data.get("access_token"), data.get("user_id")
    if not (token and uid):
        print("ERROR: no Arccos login. Set ARCCOS_ACCESS_TOKEN and ARCCOS_USER_ID, "
              f"or save them to {auth_path}. See this script's docstring.")
        sys.exit(1)
    # Cookies copied from a browser are URL-encoded
    return unquote(token), unquote(uid)


def api_get(path: str, token: str, session=None) -> dict:
    """GET an Arccos API path and return its JSON."""
    s = session or requests
    resp = s.get(API + path, headers={"Authorization": "Bearer " + token,
                                      "Accept": "application/json"}, timeout=30)
    if resp.status_code == 401:
        print("ERROR: Arccos rejected the login token (401). Get a fresh one; see the docstring.")
        sys.exit(1)
    resp.raise_for_status()
    return resp.json()


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def fetch_holes(course_id: int, version: int, n_holes: int, out_dir: Path, get) -> list[Path]:
    """Downloads every hole map. `get(path)` returns parsed JSON."""
    paths = []
    for n in range(1, n_holes + 1):
        p = out_dir / f"hole_{n:02d}.json"
        if not p.exists():
            _save(p, get(f"/courses/{course_id}/holes/{n}?courseVersion={version}"))
            time.sleep(REQUEST_GAP_S)
        paths.append(p)
    print(f"  Hole maps: {len(paths)} in {out_dir}")
    return paths


def fetch_rounds(uid: str, course_id: int, out_dir: Path, get) -> list[Path]:
    """Downloads full detail for every round you played at this course (any layout version)."""
    rounds, offset = [], 0
    while True:
        page = get(f"/v2/users/{uid}/rounds?limit=50&offSet={offset}&roundType=flagship")
        rounds += page.get("rounds") or []
        offset += 50
        if offset >= page.get("totalCount", 0) or not page.get("rounds"):
            break
    here = [r for r in rounds if r.get("courseId") == course_id
            and r.get("shouldIgnore") != "T" and r.get("isDeleted") != "T"]
    paths = []
    for r in here:
        p = out_dir / f"{r['roundId']}.json"
        if not p.exists():
            _save(p, get(f"/users/{uid}/rounds/{r['roundId']}"))
            time.sleep(REQUEST_GAP_S)
        paths.append(p)
    print(f"  Rounds at this course: {len(paths)} in {out_dir}")
    return paths


def run(slug: str, with_rounds: bool = False, get=None, uid: str | None = None) -> dict:
    cfg = load_course_config(slug)
    a = cfg.get("arccos")
    if not a:
        print('ERROR: course.json has no "arccos" block, e.g. {"course_id": 6878, "course_version": 4}')
        sys.exit(1)
    paths = get_paths(slug)
    if get is None:
        token, uid = load_auth(paths["auth"])
        session = requests.Session()
        get = lambda p: api_get(p, token, session)  # noqa: E731
    holes = fetch_holes(a["course_id"], a["course_version"], cfg.get("holes", 18), paths["holes"], get)
    rounds = []
    if with_rounds:
        rounds = fetch_rounds(uid, a["course_id"], paths["rounds"], get)
    return {"holes": holes, "rounds": rounds}


def main():
    parser = argparse.ArgumentParser(description="Download Arccos course maps (and your rounds)")
    parser.add_argument("--course", default="tedesco")
    parser.add_argument("--rounds", action="store_true", help="also download your rounds at this course")
    args = parser.parse_args()
    print(f"Arccos download for {args.course}")
    run(args.course, args.rounds)


if __name__ == "__main__":
    main()
