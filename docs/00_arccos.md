# Stage 00: Arccos download

**Script:** `pipeline/00_arccos.py`

---

## What it does

Downloads a course's hole maps from Arccos, and optionally your own rounds there:

- **Hole maps:** par, stroke index, tee distances, surface outlines (green, fairway, tee, bunker, water, trees), and an elevation strip down each hole
- **Rounds** (`--rounds`): every round you've logged at the course, with each shot's start and end GPS and the pin position

Stage 05 turns these into the course layout. This is the stage that replaces hand-tracing the course.

The map data belongs to Arccos. Keep any build from it for personal use, and don't publish it.

---

## Setup

1. Add the Arccos course to `courses/<slug>/course.json`:

   ```json
   "arccos": {"course_id": 6878, "course_version": 4}
   ```

   The course id and version are on any of your rounds there (`courseId`, `courseVersion` in the round data). Use the latest version your recent rounds were played on.

2. Get a login token:
   1. Open dashboard.arccosgolf.com, choose **Send Login Link**, and open the emailed link in a desktop browser.
   2. In the browser's dev tools (Application → Cookies), copy `access_token` and `user_id`.
   3. Save them as `courses/<slug>/data/raw/arccos/auth.json`:

      ```json
      {"access_token": "...", "user_id": "..."}
      ```

      or set `ARCCOS_ACCESS_TOKEN` and `ARCCOS_USER_ID`. The raw data folder is git-ignored, so the token never gets committed.

Tokens expire. A 401 error means get a fresh one.

---

## Usage

```bash
python pipeline/00_arccos.py --course tedesco            # hole maps only
python pipeline/00_arccos.py --course tedesco --rounds   # plus your rounds (pins, tee positions)
```

Files that already exist are skipped, so re-running is safe.

---

## Outputs

| File | Description |
|------|-------------|
| `courses/<slug>/data/raw/arccos/holes/hole_XX.json` | One Arccos hole map per hole |
| `courses/<slug>/data/raw/arccos/rounds/<roundId>.json` | One of your rounds, with shots and pin |

---

## Arccos data notes

| Field | Meaning |
|-------|---------|
| `features[].featureId` | Shape type: 1 Green, 2 Sand Trap, 3 Sand Trap by green, 4 Water, 5 Water by green, 6 Tee, 7 Fairway, 8 Fairway Center, 9 Trees, 10 Bounding Box, 11 No Shot, 12 Out Of Bounds. Anything outside the shapes is rough. From the Arccos dashboard's own lookup table. |
| `holeTees[].distance` | Tee-to-green distance in **metres**, not yards. At Tedesco, GPS tee shots match it in metres, and the course total of 5,876 is about 6,426 yards. |
| `mensHandicap` | Stroke index; 0 means not set. |
| `elevations` | 512 points on a ~5 m grid in a ~35 m strip down the hole. Good for checking the Lidar, not for terrain. |
| Round `teeId` | The tee you played, which matches `holeTees[].teeId`. |
| Shot without end GPS | A holed shot: it ended at the pin (`pinLat`, `pinLong`). |
| Hole `noOfShots` | Score: shots plus penalty strokes. |

The API addresses are at the top of `00_arccos.py`.
