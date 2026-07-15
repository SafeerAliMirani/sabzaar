"""
Fix the two real problems:

(1) MASKING. The suggestion layers were built from ESA WorldCover classes at 10 m
    and nothing else, so they paint "plant here" across rooftops, lanes and roads.
    A pin on a building is a spatial-join failure, not a vision failure. Here we
    build HARD-REJECT masks from vectors (Overture building footprints + OSM road
    centrelines buffered by class) plus water, and apply them to every suggestion
    layer. Reject, never down-rank.
    WorldCover (10 m) is deliberately NOT used as a hard reject: Larkana's built-up
    core IS class 50, and that is exactly where we want trees. One 10 m pixel covers
    ~100 CHM pixels, so a coarse label must not veto a fine one. Vectors decide.

(2) TIERING. CHM (~1 m, height, 2016 imagery) and DeepForest (~0.6 m, RGB appearance)
    fail differently, so their agreement is the confidence signal:
      tall + crown          -> "tree"        (high confidence)
      crown, not tall       -> "maybe"       (recent planting, or a false crown)
      in building/road/water-> rejected outright
    The previous hard height gate DELETED the "crown but not tall" cases, which
    throws away every tree planted since 2016. Tiering keeps them for human checking.

Outputs (all in app/data/):
  current_trees.geojson   points with tier = "tree" | "maybe"
  gaps.geojson            plantable spots, now also off-road
  priority.png            alpha zeroed on buildings/roads/water
  points_plant.bin        planting points, now vector-masked
  masks_report.json       counts, for the write-up
Run on the PC (needs internet for Overpass + WorldCover).
"""
import os, json, math, time, shutil, urllib.request
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
import numpy as np
import rasterio
from rasterio import Affine
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import from_bounds as wfb, Window
from rasterio.transform import from_bounds as t_from_bounds
from rasterio.features import rasterize
from scipy import ndimage
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app", "data")
W, S, E, N = 68.10, 27.47, 68.33, 27.65
CELL_M = 3.0
HEIGHT_MIN = float(os.environ.get("HEIGHT_MIN", 1.5))
CHM_BASE = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/chm/"
QUADKEYS = ["1231202221", "1231202230"]
WC_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
          "v200/2021/map/ESA_WorldCover_10m_2021_v200_N27E066_Map.tif")

_ml = (S + N) / 2
GW = int(round((E - W) * 111.32 * math.cos(math.radians(_ml)) * 1000 / CELL_M))
GH = int(round((N - S) * 110.57 * 1000 / CELL_M))
TF = t_from_bounds(W, S, E, N, GW, GH)
print(f"mask grid {GW}x{GH} @ {CELL_M} m", flush=True)
rep = {}

# ---------- 1. OSM road centrelines (Overpass), buffered by class ----------
ROADS = os.path.join(HERE, "_roads.geojson")
if not os.path.exists(ROADS):
    print("fetching OSM roads...", flush=True)
    q = f'[out:json][timeout:120];way["highway"]({S},{W},{N},{E});out geom;'
    req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                                 data=("data=" + q).encode(),
                                 headers={"User-Agent": "sabzaar/1.0 (contact safeer.ali.mirani@gmail.com)"})
    raw = json.loads(urllib.request.urlopen(req, timeout=180).read())
    feats = []
    for el in raw.get("elements", []):
        g = el.get("geometry")
        if not g or len(g) < 2: continue
        feats.append({"type": "Feature",
                      "properties": {"highway": el.get("tags", {}).get("highway", "road")},
                      "geometry": {"type": "LineString", "coordinates": [[p["lon"], p["lat"]] for p in g]}})
    json.dump({"type": "FeatureCollection", "features": feats}, open(ROADS, "w"))
    print(f"  {len(feats)} road ways cached", flush=True)
roads = json.load(open(ROADS))["features"]
print(f"roads: {len(roads)} ways", flush=True)

# wide classes get a wider buffer; dilation is in 3 m cells so it stays isotropic
MAJOR = {"motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link", "primary_link", "secondary_link"}
MINOR = {"tertiary", "residential", "unclassified", "service", "living_street", "tertiary_link", "road"}
maj = [(f["geometry"], 1) for f in roads if f["properties"]["highway"] in MAJOR]
mnr = [(f["geometry"], 1) for f in roads if f["properties"]["highway"] in MINOR]
road_mask = np.zeros((GH, GW), bool)
if maj:
    m = rasterize(maj, out_shape=(GH, GW), transform=TF, fill=0, default_value=1, dtype="uint8").astype(bool)
    road_mask |= ndimage.binary_dilation(m, iterations=3)      # ~9 m each side
if mnr:
    m = rasterize(mnr, out_shape=(GH, GW), transform=TF, fill=0, default_value=1, dtype="uint8").astype(bool)
    road_mask |= ndimage.binary_dilation(m, iterations=1)      # ~3 m each side
print(f"road mask covers {road_mask.mean()*100:.1f}% of bbox", flush=True)

# ---------- 2. Overture building footprints ----------
with open(os.path.join(HERE, "_buildings.geojson"), encoding="utf-8") as f:
    blds = json.load(f)
shapes = [(ft["geometry"], 1) for ft in blds["features"] if ft.get("geometry")]
build_mask = rasterize(shapes, out_shape=(GH, GW), transform=TF, fill=0, default_value=1, dtype="uint8").astype(bool)
print(f"buildings: {len(shapes)} polygons, {build_mask.mean()*100:.1f}% of bbox", flush=True)


def read_grid(url, gh, gw, tf, resampling=Resampling.max, band=1):
    dst = np.full((gh, gw), np.nan, np.float32)
    with rasterio.open(url) as s:
        epsg = s.crs.to_epsg()
        l, b, r, t = (W, S, E, N) if epsg == 4326 else transform_bounds("EPSG:4326", s.crs, W, S, E, N, densify_pts=21)
        win = wfb(l, b, r, t, transform=s.transform)
        try: win = win.intersection(Window(0, 0, s.width, s.height))
        except Exception: return dst
        if win.width < 1 or win.height < 1: return dst
        rw = max(1, min(int(round(win.width)), gw)); rh = max(1, min(int(round(win.height)), gh))
        arr = s.read(band, window=win, out_shape=(rh, rw), resampling=Resampling.average, masked=True).astype("float32").filled(np.nan)
        rt = s.window_transform(win) * Affine.scale(win.width / rw, win.height / rh)
        reproject(arr, dst, src_transform=rt, src_crs=s.crs, dst_transform=tf, dst_crs="EPSG:4326",
                  resampling=resampling, src_nodata=np.nan, dst_nodata=np.nan)
    return dst


# ---------- 3. water (WorldCover class 80) = hard reject ----------
print("reading WorldCover...", flush=True)
lc = np.nan_to_num(read_grid(WC_URL, GH, GW, TF, Resampling.mode), nan=0).astype(np.int16)
water_mask = (lc == 80)
print(f"water {water_mask.mean()*100:.1f}%", flush=True)
del lc

REJECT = build_mask | road_mask | water_mask
rep["reject_pct_of_bbox"] = round(float(REJECT.mean()) * 100, 2)
rep["road_pct"] = round(float(road_mask.mean()) * 100, 2)
rep["building_pct"] = round(float(build_mask.mean()) * 100, 2)


def gxy(lon, lat):
    ix = min(GW - 1, max(0, int((lon - W) / (E - W) * GW)))
    iy = min(GH - 1, max(0, int((N - lat) / (N - S) * GH)))
    return ix, iy


# ---------- 4. CHM height (for the tiering) ----------
print("reading CHM height...", flush=True)
CH, CWx = GH // 2, GW // 2                       # ~6 m grid is plenty for a tall/not-tall test
CTF = t_from_bounds(W, S, E, N, CWx, CH)
chm = np.full((CH, CWx), np.nan, np.float32)
for qk in QUADKEYS:
    g = read_grid(CHM_BASE + qk + ".tif", CH, CWx, CTF)
    m = np.isfinite(g); chm[m] = np.fmax(np.nan_to_num(chm[m], nan=0.0), g[m])
print(f"  CHM covers {np.isfinite(chm).mean()*100:.0f}%", flush=True)


def chm_tall(lon, lat):
    ix = min(CWx - 1, max(0, int((lon - W) / (E - W) * CWx)))
    iy = min(CH - 1, max(0, int((N - lat) / (N - S) * CH)))
    sub = chm[max(0, iy-1):iy+2, max(0, ix-1):ix+2]
    if not np.isfinite(sub).any(): return None          # no CHM cover -> unknown
    return float(np.nanmax(sub)) >= HEIGHT_MIN


# ---------- 5. tier the detections (from the raw pre-gate set) ----------
GEO = os.path.join(OUT, "current_trees.geojson")
src = GEO + ".prefilter" if os.path.exists(GEO + ".prefilter") else GEO
feats = json.load(open(src))["features"]
print(f"tiering {len(feats)} raw detections from {os.path.basename(src)}", flush=True)
out, n_tree, n_maybe, n_rej_mask = [], 0, 0, 0
for f in feats:
    lo, la = f["geometry"]["coordinates"]
    ix, iy = gxy(lo, la)
    if REJECT[iy, ix]:
        n_rej_mask += 1; continue                       # on a building / road / water -> reject
    tall = chm_tall(lo, la)
    tier = "tree" if tall else "maybe"                  # unknown CHM cover -> maybe, not deleted
    f["properties"]["tier"] = tier
    out.append(f); n_tree += tier == "tree"; n_maybe += tier == "maybe"
json.dump({"type": "FeatureCollection", "features": out}, open(GEO, "w"))
rep["detections_raw"] = len(feats); rep["detections_rejected_by_mask"] = n_rej_mask
rep["detections_tree"] = n_tree; rep["detections_maybe"] = n_maybe
print(f"  tree={n_tree}  maybe={n_maybe}  rejected_by_mask={n_rej_mask}", flush=True)

# ---------- 6. gaps.geojson: add the road/water reject ----------
GAPS = os.path.join(OUT, "gaps.geojson")
if os.path.exists(GAPS):
    g = json.load(open(GAPS))["features"]
    keep = [f for f in g if not REJECT[gxy(*f["geometry"]["coordinates"])[1], gxy(*f["geometry"]["coordinates"])[0]]]
    shutil.copy(GAPS, GAPS + ".prefilter")
    json.dump({"type": "FeatureCollection", "features": keep}, open(GAPS, "w"))
    rep["gaps_before"] = len(g); rep["gaps_after"] = len(keep)
    print(f"gaps: {len(g)} -> {len(keep)}", flush=True)

# ---------- 7. priority.png: zero alpha on rejects ----------
PRI = os.path.join(OUT, "priority.png")
if os.path.exists(PRI):
    im = Image.open(PRI).convert("RGBA"); a = np.array(im)
    ph, pw = a.shape[:2]
    yy = (np.arange(ph) * GH // ph).clip(0, GH-1)
    xx = (np.arange(pw) * GW // pw).clip(0, GW-1)
    rej_small = REJECT[yy][:, xx]
    before = int((a[..., 3] > 0).sum())
    a[..., 3] = np.where(rej_small, 0, a[..., 3])
    after = int((a[..., 3] > 0).sum())
    shutil.copy(PRI, PRI + ".prefilter")
    Image.fromarray(a, "RGBA").save(PRI, optimize=True)
    rep["priority_px_before"] = before; rep["priority_px_after"] = after
    rep["priority_px_removed_pct"] = round((before-after)/max(1, before)*100, 1)
    print(f"priority.png: {before} -> {after} lit px ({rep['priority_px_removed_pct']}% removed)", flush=True)

# ---------- 8. points_plant.bin: vector-mask the planting points ----------
PB = os.path.join(OUT, "points_plant.bin")
if os.path.exists(PB):
    xy = np.fromfile(PB, np.float32).reshape(-1, 2)
    ixs = ((xy[:, 0] - W) / (E - W) * GW).astype(int).clip(0, GW-1)
    iys = ((N - xy[:, 1]) / (N - S) * GH).astype(int).clip(0, GH-1)
    keep = ~REJECT[iys, ixs]
    shutil.copy(PB, PB + ".prefilter")
    xy[keep].astype(np.float32).tofile(PB)
    rep["plant_points_before"] = int(len(xy)); rep["plant_points_after"] = int(keep.sum())
    print(f"points_plant: {len(xy)} -> {int(keep.sum())}", flush=True)

json.dump(rep, open(os.path.join(OUT, "masks_report.json"), "w"), indent=1)
print("\n== REPORT ==\n" + json.dumps(rep, indent=1), flush=True)
