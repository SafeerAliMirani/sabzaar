"""
Build the ground-check list: the 50 sites Sabzaar most recommends, in a form a
human can actually go and test.

The honest metric is "of our top 50 suggested sites, how many pass a ground check?"
- not "is the map 100% accurate". This produces that list.

Ranking (all from data already on disk, no new fetches):
  need      - how built-up/congested the block is. find_gaps.py already sorted
              gaps.geojson by built_frac descending, so a spot's position in the
              file IS its congestion rank. Built-up density is also the urban-heat
              driver, so this doubles as the heat signal.
  isolation - distance to the nearest DETECTED tree. A gap with no tree near it
              needs one more than a gap beside a mature canopy.
  room      - measured clearance to the nearest obstacle (the "m" property).
Sites are then picked greedily with a minimum separation so the 50 are spread
across the city, not 50 spots on one lane.

Outputs:
  top50_ground_check.csv   rank, lat, lon, clearance, why, maps link, verdict column
  TOP50_GROUND_CHECK.md    same, clickable, ready to fill in on a phone
"""
import os, json, math, csv

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app", "data")
N_PICK = 50
MIN_SEP_M = 250.0          # keep the 50 spread across the city

gaps = json.load(open(os.path.join(OUT, "gaps.geojson")))["features"]
trees = json.load(open(os.path.join(OUT, "current_trees.geojson")))["features"]
print(f"{len(gaps)} masked plantable spots, {len(trees)} detected trees", flush=True)

MLAT = 27.56
M_PER_DEG_LAT = 110570.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(MLAT))


def m_dist(a, b):
    dx = (a[0] - b[0]) * M_PER_DEG_LON
    dy = (a[1] - b[1]) * M_PER_DEG_LAT
    return math.hypot(dx, dy)


# coarse spatial hash of trees so nearest-tree lookup is cheap
CELL = 0.0025                     # ~250 m
grid = {}
for t in trees:
    lo, la = t["geometry"]["coordinates"]
    grid.setdefault((int(lo / CELL), int(la / CELL)), []).append((lo, la))


def nearest_tree_m(lo, la):
    gx, gy = int(lo / CELL), int(la / CELL)
    best = 9999.0
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for p in grid.get((gx + dx, gy + dy), ()):
                d = m_dist((lo, la), p)
                if d < best: best = d
    return best


n = len(gaps)
scored = []
for i, f in enumerate(gaps):
    lo, la = f["geometry"]["coordinates"]
    clear_m = float(f["properties"].get("m", 2.5))
    need = 1.0 - (i / max(1, n - 1))          # file order = congestion rank
    iso = min(nearest_tree_m(lo, la), 150.0) / 150.0
    room = min(clear_m, 12.0) / 12.0
    score = 0.50 * need + 0.30 * iso + 0.20 * room
    scored.append({"lo": lo, "la": la, "clear": clear_m, "need": need,
                   "iso_m": min(nearest_tree_m(lo, la), 150.0), "score": score})

scored.sort(key=lambda r: -r["score"])

picked = []
for r in scored:
    if all(m_dist((r["lo"], r["la"]), (p["lo"], p["la"])) >= MIN_SEP_M for p in picked):
        picked.append(r)
        if len(picked) >= N_PICK: break
print(f"picked {len(picked)} sites, min separation {MIN_SEP_M:.0f} m", flush=True)

with open(os.path.join(HERE, "top50_ground_check.csv"), "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["rank", "lat", "lon", "clearance_m", "nearest_tree_m", "maps_link",
                "verdict (pass/fail)", "notes"])
    for k, r in enumerate(picked, 1):
        w.writerow([k, round(r["la"], 6), round(r["lo"], 6), round(r["clear"], 1),
                    round(r["iso_m"]), f"https://www.google.com/maps?q={r['la']:.6f},{r['lo']:.6f}", "", ""])

lines = ["# Sabzaar - top 50 ground check", "",
         "The honest metric: **of these 50 suggested sites, how many pass a ground check?**",
         "A site passes if a tree could actually go there: real open ground, not a roof/road/drain,",
         "and plausibly someone could water it. Mark pass or fail - a fail is useful data, not a defeat.",
         "", f"Picked from {len(gaps)} masked plantable spots, spread >= {MIN_SEP_M:.0f} m apart.",
         "Ranked by block congestion (also the heat driver), distance from the nearest detected tree, and measured room.",
         "", "| # | site | room | nearest tree | verdict |", "|---|---|---|---|---|"]
for k, r in enumerate(picked, 1):
    lines.append(f"| {k} | [{r['la']:.5f}, {r['lo']:.5f}](https://www.google.com/maps?q={r['la']:.6f},{r['lo']:.6f}) "
                 f"| {r['clear']:.1f} m | {round(r['iso_m'])} m | |")
lines += ["", "**Score:** ____ / 50 passed.  Date: ________  Checked by: ________"]
open(os.path.join(HERE, "TOP50_GROUND_CHECK.md"), "w", encoding="utf-8").write("\n".join(lines))
print("wrote top50_ground_check.csv + TOP50_GROUND_CHECK.md", flush=True)
