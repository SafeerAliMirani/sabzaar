"""
Sabzaar data pipeline.

Fetches REAL public satellite data, clips it to Larkana, and renders web-ready
overlays for the static MapLibre app. No synthetic data, no accounts, no tokens.

Sources (all verified live 2026-07-07, see DATA_SOURCES.md):
  1. Canopy height ~1 m : Meta + WRI CHM v2  (CC-BY 4.0)
  2. Land cover   10 m  : ESA WorldCover 2021 v200  (CC-BY 4.0)
  3. Heat  (LST)  30 m  : Landsat C2 L2 ST_B10 via MS Planetary Computer (public domain)

Outputs into app/data/:
  canopy.png, landcover.png, heat.png   (RGBA overlays, EPSG:4326, stretched to BBOX)
  meta.json                             (bounds, capture dates, honest stats, provenance)

Run on a machine with internet (the CHM/WorldCover/Landsat hosts are not reachable
from the sandbox). Tested with Python 3.12 + rasterio.
"""

import os
import io
import json
import math
import sys
import datetime as dt

import numpy as np
import requests
import rasterio
from rasterio import Affine
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import from_bounds as win_from_bounds, Window
from rasterio.transform import from_bounds as t_from_bounds
from PIL import Image

# GDAL / vsicurl tuning for fast windowed remote reads
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MULTIPLEX", "YES")
os.environ.setdefault("VSI_CACHE", "TRUE")
os.environ.setdefault("VSI_CACHE_SIZE", "134217728")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "40")
os.environ.setdefault("CPL_VSIL_CURL_USE_HEAD", "NO")
os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")
os.environ.setdefault("GDAL_BAND_BLOCK_CACHE", "HASHSET")

# --- Larkana clip box: W, S, E, N (lon/lat) ---------------------------------
W, S, E, N = 68.10, 27.47, 68.33, 27.65
DST_CRS = "EPSG:4326"

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app", "data")
os.makedirs(OUT, exist_ok=True)

# ground aspect so stored PNGs are not distorted (image still stretched to BBOX)
_mean_lat = (S + N) / 2.0
GROUND_W = (E - W) * 111.32 * math.cos(math.radians(_mean_lat))
GROUND_H = (N - S) * 110.57
ASPECT = GROUND_H / GROUND_W  # height / width


def grid(width):
    return width, int(round(width * ASPECT))


def read_into_master(url, mw, mh, resampling, band=1):
    """Read a remote raster clipped to Larkana onto the (mh, mw) EPSG:4326 grid.

    A decimated windowed read lets GDAL use the source's overviews (small
    network read). If the source is already in EPSG:4326 (WorldCover) we read it
    directly - fast and proven. Otherwise we reproject the small clipped array
    locally. NaN where there is no data."""
    # some resamplings (max/min/med/q1/q3) are valid only for warping, not for a
    # decimated read; read with a safe method and let the warp do the clever part.
    READ_OK = {Resampling.nearest, Resampling.bilinear, Resampling.cubic,
               Resampling.cubic_spline, Resampling.lanczos, Resampling.average,
               Resampling.mode, Resampling.gauss}
    read_rs = resampling if resampling in READ_OK else Resampling.average
    dst_transform = t_from_bounds(W, S, E, N, mw, mh)
    dst = np.full((mh, mw), np.nan, np.float32)
    with rasterio.open(url) as src:
        epsg = src.crs.to_epsg() if src.crs else None
        # source bounds -> our box, clipped to the raster
        if epsg == 4326:
            l, b, r, t = W, S, E, N
        else:
            l, b, r, t = transform_bounds(DST_CRS, src.crs, W, S, E, N, densify_pts=21)
        win = win_from_bounds(l, b, r, t, transform=src.transform)
        try:
            win = win.intersection(Window(0, 0, src.width, src.height))
        except rasterio.errors.WindowError:
            return dst
        if win.width < 1 or win.height < 1:
            return dst
        # read at the output size so GDAL serves it from an overview (fast),
        # instead of forcing a full-resolution download
        rw = max(1, min(int(round(win.width)), mw))
        rh = max(1, min(int(round(win.height)), mh))
        arr = src.read(band, window=win, out_shape=(rh, rw),
                       resampling=read_rs, masked=True).astype("float32").filled(np.nan)
        read_transform = src.window_transform(win) * Affine.scale(win.width / rw, win.height / rh)
        warp_rs = Resampling.nearest if epsg == 4326 else resampling
        reproject(arr, dst, src_transform=read_transform, src_crs=src.crs,
                  dst_transform=dst_transform, dst_crs=DST_CRS,
                  resampling=warp_rs, src_nodata=np.nan, dst_nodata=np.nan)
    return dst


def lerp(c0, c1, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c0, c1))


def ramp(value, stops):
    """stops: list of (position0..1, (r,g,b)). Returns (r,g,b)."""
    if value <= stops[0][0]:
        return stops[0][1]
    if value >= stops[-1][0]:
        return stops[-1][1]
    for i in range(1, len(stops)):
        if value <= stops[i][0]:
            p0, c0 = stops[i - 1]
            p1, c1 = stops[i]
            return lerp(c0, c1, (value - p0) / (p1 - p0))
    return stops[-1][1]


def save_rgba(arr_rgba, path):
    Image.fromarray(arr_rgba, "RGBA").save(path, optimize=True)
    kb = os.path.getsize(path) / 1024
    print(f"    wrote {os.path.basename(path)}  {arr_rgba.shape[1]}x{arr_rgba.shape[0]}  {kb:.0f} KB")


# quadkeys of the zoom-10 CHM tiles intersecting the box
def bbox_quadkeys(zoom=10):
    def tile(lon, lat):
        n = 2 ** zoom
        x = int((lon + 180.0) / 360.0 * n)
        lat_r = math.radians(lat)
        y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
        return x, y

    def quadkey(x, y, z):
        qk = ""
        for i in range(z, 0, -1):
            d = 0
            mask = 1 << (i - 1)
            if x & mask:
                d += 1
            if y & mask:
                d += 2
            qk += str(d)
        return qk

    x0, y0 = tile(W, N)
    x1, y1 = tile(E, S)
    keys = set()
    for x in range(min(x0, x1), max(x0, x1) + 1):
        for y in range(min(y0, y1), max(y0, y1) + 1):
            keys.add(quadkey(x, y, zoom))
    return sorted(keys)


meta = {
    "place": "Larkana, Sindh, Pakistan",
    "center": [round((W + E) / 2, 5), round((S + N) / 2, 5)],
    "bbox": [W, S, E, N],
    "generated": dt.datetime.utcnow().strftime("%Y-%m-%d"),
    "layers": {},
}

# ============================================================ 1. CANOPY (CHM v2)
print("[1/3] Canopy height  (Meta + WRI CHM v2, ~1 m, CC-BY 4.0)")
CHM_BASE = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/chm/"
dmw, dmh = grid(4000)  # reads the /8 overview (cheap); crisp display comes from the PMTiles, this array is for stats + points
canopy = np.full((dmh, dmw), np.nan, np.float32)
used = []
for qk in bbox_quadkeys(10):
    url = f"{CHM_BASE}{qk}.tif"
    try:
        part = read_into_master(url, dmw, dmh, Resampling.average)
    except Exception as ex:
        print(f"    - {qk}: skip ({str(ex)[:55]})")
        continue
    if np.isnan(part).all():
        continue
    used.append(qk)
    canopy = np.where(np.isnan(canopy), part, np.fmax(canopy, np.nan_to_num(part, nan=-1)))
    canopy = np.where(canopy < 0, np.nan, canopy)
    print(f"    + {qk}: merged")
disp_m = round((E - W) * 111.32 * math.cos(math.radians(_mean_lat)) * 1000 / dmw, 1)

valid = ~np.isnan(canopy)
if valid.any():
    xp = [1, 5, 10, 15]
    hh = np.where(valid, canopy, 0.0)
    show = valid & (canopy >= 1.0)
    rgba = np.zeros((dmh, dmw, 4), np.uint8)
    rgba[..., 0] = np.interp(hh, xp, [173, 95, 40, 12]).astype(np.uint8)
    rgba[..., 1] = np.interp(hh, xp, [209, 170, 120, 70]).astype(np.uint8)
    rgba[..., 2] = np.interp(hh, xp, [158, 90, 55, 30]).astype(np.uint8)
    alpha = 135 + np.clip((hh - 1) / 9.0, 0, 1) * 105
    rgba[..., 3] = np.where(show, alpha, 0).astype(np.uint8)
    save_rgba(rgba, os.path.join(OUT, "canopy.png"))

    TREE = 3.0  # metres: a "tree" for cover stats (honest threshold, not species)
    tree_mask = valid & (canopy >= TREE)
    meta["layers"]["canopy"] = {
        "title": "Tree canopy height",
        "source": "Meta & WRI High-Resolution Canopy Height v2 (CHMv2)",
        "license": "CC-BY 4.0",
        "resolution_m": 1,
        "display_m": disp_m,
        "tier": "measured",
        "tiles_used": used,
        "note": "Canopy height from a machine-learning model on ~1 m imagery. It reads height, so it separates trees (tall) from grass, crops and parks (low) - not by colour. Shown at about " + str(disp_m) + " m per pixel; zoom in to pick out individual trees.",
        "stats": {
            "tree_cover_pct": round(float(tree_mask.sum()) / float(valid.sum()) * 100, 1),
            "mean_height_trees_m": round(float(np.nanmean(canopy[tree_mask])), 1) if tree_mask.any() else 0,
            "max_height_m": round(float(np.nanmax(canopy[valid])), 1),
            "tree_threshold_m": TREE,
        },
    }
    print(f"    tree cover >= {TREE} m: {meta['layers']['canopy']['stats']['tree_cover_pct']}%  (display ~{disp_m} m/px)")
else:
    print("    ! no canopy coverage found")

# ====================================================== 2. LAND COVER (WorldCover)
print("[2/3] Land cover  (ESA WorldCover 2021 v200, 10 m, CC-BY 4.0)")
WC_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
          "v200/2021/map/ESA_WorldCover_10m_2021_v200_N27E066_Map.tif")
WC_COLORS = {
    10: (0, 100, 0), 20: (255, 187, 34), 30: (255, 255, 76), 40: (240, 150, 255),
    50: (250, 0, 0), 60: (180, 180, 180), 70: (240, 240, 240), 80: (0, 100, 200),
    90: (0, 150, 160), 95: (0, 207, 117), 100: (250, 230, 160),
}
WC_NAMES = {
    10: "Tree cover", 20: "Shrubland", 30: "Grassland", 40: "Cropland",
    50: "Built-up", 60: "Bare / sparse", 70: "Snow / ice", 80: "Water",
    90: "Wetland", 95: "Mangrove", 100: "Moss / lichen",
}
lmw, lmh = grid(2200)
try:
    lc = read_into_master(WC_URL, lmw, lmh, Resampling.mode)
    lvalid = ~np.isnan(lc)
    lci = np.where(lvalid, np.round(lc), 0).astype(np.int32)
    rgba = np.zeros((lmh, lmw, 4), np.uint8)
    counts = {}
    for cls, col in WC_COLORS.items():
        m = lci == cls
        if m.any():
            rgba[m] = (col[0], col[1], col[2], 175)
            counts[cls] = int(m.sum())
    save_rgba(rgba, os.path.join(OUT, "landcover.png"))
    tot = sum(counts.values()) or 1
    breakdown = [
        {"class": cls, "name": WC_NAMES[cls],
         "color": "#%02x%02x%02x" % WC_COLORS[cls],
         "pct": round(counts[cls] / tot * 100, 1)}
        for cls in sorted(counts, key=lambda c: -counts[c])
    ]
    meta["layers"]["landcover"] = {
        "title": "Land cover",
        "source": "ESA WorldCover 2021 (v200)",
        "license": "CC-BY 4.0",
        "resolution_m": 10,
        "tier": "measured",
        "year": 2021,
        "note": "Independent 10 m land-cover map. Cross-check for the greenery picture.",
        "breakdown": breakdown,
    }
    top = breakdown[0]
    print(f"    dominant class: {top['name']} {top['pct']}%")
except Exception as ex:
    print(f"    ! land cover failed: {ex}")

# ============================================================= 3. HEAT (Landsat LST)
print("[3/3] Surface temperature  (Landsat C2 L2 ST_B10, 30 m, public domain)")
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"
try:
    body = {
        "collections": ["landsat-c2-l2"],
        "bbox": [W, S, E, N],
        "datetime": "2026-04-01T00:00:00Z/2026-07-07T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": 10},
                  "platform": {"in": ["landsat-8", "landsat-9"]}},
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
        "limit": 5,
    }
    feats = requests.post(STAC, json=body, timeout=60).json()["features"]
    # prefer the least cloudy, most recent summer scene
    feats.sort(key=lambda f: (f["properties"]["eo:cloud_cover"],
                              -int(f["properties"]["datetime"][:10].replace("-", ""))))
    item = feats[0]
    href = item["assets"]["lwir11"]["href"]
    signed = requests.get(SIGN, params={"href": href}, timeout=60).json()["href"]
    hmw, hmh = grid(820)
    raw = read_into_master(signed, hmw, hmh, Resampling.average)
    hvalid = ~np.isnan(raw) & (raw > 0)
    celsius = np.full_like(raw, np.nan)
    celsius[hvalid] = raw[hvalid] * 0.00341802 + 149.0 - 273.15
    vmin = float(np.nanpercentile(celsius[hvalid], 2))
    vmax = float(np.nanpercentile(celsius[hvalid], 98))
    span = max(0.1, vmax - vmin)
    t = np.clip((celsius - vmin) / span, 0, 1)
    t = np.where(hvalid, t, 0.0)
    xp = [0.0, 0.5, 1.0]
    rgba = np.zeros((hmh, hmw, 4), np.uint8)
    rgba[..., 0] = np.interp(t, xp, [69, 255, 215]).astype(np.uint8)
    rgba[..., 1] = np.interp(t, xp, [117, 255, 48]).astype(np.uint8)
    rgba[..., 2] = np.interp(t, xp, [180, 191, 39]).astype(np.uint8)
    rgba[..., 3] = np.where(hvalid, 170, 0).astype(np.uint8)
    save_rgba(rgba, os.path.join(OUT, "heat.png"))
    meta["layers"]["heat"] = {
        "title": "Land surface temperature",
        "source": "Landsat Collection 2 Level-2 (ST_B10), USGS/NASA via MS Planetary Computer",
        "license": "Public domain (USGS/NASA)",
        "resolution_m": 30,
        "tier": "measured",
        "scene": item["id"],
        "date": item["properties"]["datetime"][:10],
        "cloud_cover_pct": item["properties"]["eo:cloud_cover"],
        "note": "Ground (surface) temperature on one clear day, not air temperature and not a climate average.",
        "stats": {
            "min_c": round(float(np.nanmin(celsius[hvalid])), 1),
            "mean_c": round(float(np.nanmean(celsius[hvalid])), 1),
            "max_c": round(float(np.nanmax(celsius[hvalid])), 1),
            "display_min_c": round(vmin, 1),
            "display_max_c": round(vmax, 1),
        },
    }
    s = meta["layers"]["heat"]
    print(f"    {s['scene']}  {s['date']}  cloud={s['cloud_cover_pct']}%  "
          f"LST {s['stats']['min_c']}..{s['stats']['max_c']} C")
except Exception as ex:
    print(f"    ! heat failed: {ex}")

# ================================================= DERIVED: where to plant
# The most useful output: combine the three measured layers to find land where
# new shade trees would help most - hot, with little or no canopy, and not
# farmland or water. This is guidance derived from data, not a raw measurement.
print("[+] Priority planting spaces  (derived from the three layers above)")
try:
    AW, AH = lmw, lmh  # analysis grid = the 10 m land-cover grid

    def _resize(a, mode):
        return np.asarray(Image.fromarray(a.astype("float32"), "F").resize((AW, AH), mode), dtype="float32")

    canopy_a = _resize(np.nan_to_num(canopy, nan=0.0), Image.BILINEAR)
    lst_fill = float(np.nanmean(celsius)) if np.isfinite(celsius).any() else 0.0
    lst_a = _resize(np.where(np.isnan(celsius), lst_fill, celsius), Image.BILINEAR)
    lc_a = lci  # already on this grid (ints)

    # OPEN, unbuilt land only. We deliberately drop built-up (50): at 10 m a
    # built-up cell is rooftops and walls, not plantable ground. Larkana is
    # congested, so flagging houses as "plant here" is wrong - the dense core
    # needs street-level solutions (see the app's how-to-plant note), not this map.
    PLANTABLE = [20, 30, 60]   # shrubland, grassland, bare/sparse
    plantable = np.isin(lc_a, PLANTABLE)      # excludes built-up, cropland, water, wetland, existing tree
    treeless = canopy_a < 2.0                 # not already shaded
    plant_mask = plantable & treeless

    if plant_mask.any():
        t_lo = float(np.nanpercentile(lst_a[plant_mask], 40))
        t_hi = float(np.nanpercentile(lst_a[plant_mask], 95))
        score = np.clip((lst_a - t_lo) / max(0.1, t_hi - t_lo), 0, 1)
        # warm-yellow (helpful) -> orange -> magenta (hottest, plant first)
        rgba = np.zeros((AH, AW, 4), np.uint8)
        xp = [0.0, 0.5, 1.0]
        rgba[..., 0] = np.interp(score, xp, [255, 240, 190]).astype(np.uint8)
        rgba[..., 1] = np.interp(score, xp, [214, 120, 25]).astype(np.uint8)
        rgba[..., 2] = np.interp(score, xp, [95, 40, 105]).astype(np.uint8)
        rgba[..., 3] = np.where(plant_mask, 205, 0).astype(np.uint8)
        save_rgba(rgba, os.path.join(OUT, "priority.png"))

        cell_km2 = ((E - W) * 111.32 * math.cos(math.radians(_mean_lat)) / AW) * ((N - S) * 110.57 / AH)
        area_ha = float(plant_mask.sum()) * cell_km2 * 100.0
        hot_mask = plant_mask & (lst_a >= t_hi)
        meta["layers"]["priority"] = {
            "title": "Where to plant",
            "source": "Derived from CHM canopy + WorldCover land cover + Landsat heat",
            "tier": "guidance",
            "resolution_m": 10,
            "note": "Open, unbuilt land that is hot with little or no canopy - vacant plots, bare ground, scrub, park and canal edges. It deliberately skips rooftops and built-up areas (no planting space) and farmland and water. Brighter, more magenta = hotter, plant first. Guidance from data, not a site survey.",
            "method": "land cover in {shrubland, grassland, bare} (built-up excluded) AND canopy under 2 m, shaded by surface temperature.",
            "stats": {
                "area_ha": round(area_ha),
                "priority_range_c": [round(t_lo, 1), round(t_hi, 1)],
            },
        }
        print(f"    candidate planting land ~ {round(area_ha):,} ha; hottest band >= {round(t_hi,1)} C")
    else:
        print("    ! no plantable cells found")
except Exception as ex:
    print(f"    ! priority failed: {ex}")

# ================================================= GPU point clouds (deck.gl)
# Two point layers rendered on the GPU in the browser: existing tree canopy and
# open planting spots. Positions only (Float32 lon,lat); coloured per layer.
print("[+] GPU point clouds")
try:
    def cell_lonlat(mw, mh):
        xs = (W + (np.arange(mw) + 0.5) / mw * (E - W)).astype(np.float32)
        ys = (N - (np.arange(mh) + 0.5) / mh * (N - S)).astype(np.float32)
        return xs, ys

    rng = np.random.default_rng(0)
    pts = {}

    # existing trees: fine canopy grid, cells >= 3 m
    tsel = (~np.isnan(canopy)) & (canopy >= 3.0)
    ys_i, xs_i = np.where(tsel)
    xs, ys = cell_lonlat(canopy.shape[1], canopy.shape[0])
    tlon, tlat = xs[xs_i], ys[ys_i]
    TCAP = 700000
    if tlon.size > TCAP:
        keep = rng.choice(tlon.size, TCAP, replace=False)
        tlon, tlat = tlon[keep], tlat[keep]
    txy = np.empty((tlon.size, 2), np.float32)
    txy[:, 0], txy[:, 1] = tlon, tlat
    txy.tofile(os.path.join(OUT, "points_trees.bin"))
    pts["trees"] = int(txy.shape[0])

    # planting spots: the open plant_mask (analysis grid)
    try:
        ys2, xs2 = np.where(plant_mask)
        xa, ya = cell_lonlat(plant_mask.shape[1], plant_mask.shape[0])
        plon, plat = xa[xs2], ya[ys2]
        PCAP = 600000
        if plon.size > PCAP:
            keep = rng.choice(plon.size, PCAP, replace=False)
            plon, plat = plon[keep], plat[keep]
        pxy = np.empty((plon.size, 2), np.float32)
        pxy[:, 0], pxy[:, 1] = plon, plat
        pxy.tofile(os.path.join(OUT, "points_plant.bin"))
        pts["plant"] = int(pxy.shape[0])
    except Exception as ex:
        print("    ! plant points:", ex)

    meta["points"] = pts
    print(f"    tree points: {pts.get('trees')}  plant points: {pts.get('plant')}")
except Exception as ex:
    print(f"    ! points failed: {ex}")

with open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8") as f:
    json.dump(meta, f, indent=2)
print("done -> app/data/meta.json")
