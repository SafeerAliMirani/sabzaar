"""
Find plantable-gap candidates in Larkana's URBAN core: open ground where a tree
would physically fit - not on a building, not an existing tree, not water/farmland -
ranked by clearance. Output app/data/gaps.geojson for a clickable map layer.

Inputs:
  _buildings.geojson  - Overture building footprints (run: overturemaps download ...)
  Meta/WRI CHM canopy + ESA WorldCover (read on the fly, same as convert.py)
"""
import os
import json
import math

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "40")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
import numpy as np
import rasterio
from rasterio import Affine
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import from_bounds as win_from_bounds, Window
from rasterio.transform import from_bounds as t_from_bounds
from rasterio.features import rasterize
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app", "data")
W, S, E, N = 68.10, 27.47, 68.33, 27.65
DST_CRS = "EPSG:4326"
CELL_M = 3.0                       # analysis resolution (metres)
CLEAR_MIN = 3.0                    # min clearance to nearest obstacle for a spot (m)
SEP_CELLS = 13                     # spacing between candidates (~40 m)
CAP = 4000
CHM_BASE = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/chm/"
QUADKEYS = ["1231202221", "1231202230"]
WC_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
          "v200/2021/map/ESA_WorldCover_10m_2021_v200_N27E066_Map.tif")

_mean_lat = (S + N) / 2
GW = int(round((E - W) * 111.32 * math.cos(math.radians(_mean_lat)) * 1000 / CELL_M))
GH = int(round((N - S) * 110.57 * 1000 / CELL_M))
TF = t_from_bounds(W, S, E, N, GW, GH)
print(f"grid {GW}x{GH} @ ~{CELL_M} m", flush=True)


def read_into_master(url, mw, mh, resampling, band=1):
    READ_OK = {Resampling.nearest, Resampling.bilinear, Resampling.cubic, Resampling.average, Resampling.mode}
    read_rs = resampling if resampling in READ_OK else Resampling.average
    dst = np.full((mh, mw), np.nan, np.float32)
    with rasterio.open(url) as src:
        epsg = src.crs.to_epsg() if src.crs else None
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
        rw = max(1, min(int(round(win.width)), mw))
        rh = max(1, min(int(round(win.height)), mh))
        arr = src.read(band, window=win, out_shape=(rh, rw), resampling=read_rs, masked=True).astype("float32").filled(np.nan)
        rt = src.window_transform(win) * Affine.scale(win.width / rw, win.height / rh)
        warp_rs = Resampling.nearest if epsg == 4326 else resampling
        reproject(arr, dst, src_transform=rt, src_crs=src.crs, dst_transform=TF, dst_crs=DST_CRS,
                  resampling=warp_rs, src_nodata=np.nan, dst_nodata=np.nan)
    return dst


# ---- land cover (10 m, fast): urban mask, existing tree cover, no-plant classes ----
print("reading land cover...", flush=True)
lc = np.nan_to_num(read_into_master(WC_URL, GW, GH, Resampling.mode), nan=0).astype(np.int16)
builtup = lc == 50
tree = lc == 10                                 # WorldCover tree cover as the existing-tree mask
exclude_lc = np.isin(lc, [40, 80, 90, 95, 0])   # cropland, water, wetland, mangrove, nodata
del lc

# ---- buildings ----
print("rasterizing buildings...", flush=True)
with open(os.path.join(HERE, "_buildings.geojson"), encoding="utf-8") as f:
    blds = json.load(f)
shapes = [(ft["geometry"], 1) for ft in blds["features"] if ft.get("geometry")]
building = rasterize(shapes, out_shape=(GH, GW), transform=TF, fill=0, default_value=1, dtype="uint8").astype(bool)
print(f"  {len(shapes)} building polygons", flush=True)

# ---- urban focus: within ~150 m of built-up land ----
urban = ndimage.binary_dilation(builtup, iterations=int(round(150 / CELL_M)))

# ---- plantable: open ground in the urban core ----
plantable = urban & (~building) & (~tree) & (~exclude_lc)
del urban, exclude_lc

# ---- clearance to nearest obstacle, then pick well-spaced local maxima ----
print("distance transform...", flush=True)
dist = ndimage.distance_transform_edt(plantable).astype(np.float32) * CELL_M
peaks = (dist >= CLEAR_MIN) & (dist == ndimage.maximum_filter(dist, size=SEP_CELLS))
ys, xs = np.where(peaks)
clear = dist[ys, xs]
print(f"  {clear.size} raw candidates", flush=True)

order = np.argsort(-clear)[:CAP]
feats = []
for i in order:
    lon = W + (xs[i] + 0.5) / GW * (E - W)
    lat = N - (ys[i] + 0.5) / GH * (N - S)
    feats.append({"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": [round(float(lon), 6), round(float(lat), 6)]},
                  "properties": {"m": round(float(clear[i]), 1)}})

with open(os.path.join(OUT, "gaps.geojson"), "w", encoding="utf-8") as f:
    json.dump({"type": "FeatureCollection", "features": feats}, f)
print(f"wrote app/data/gaps.geojson  ({len(feats)} plantable spots)", flush=True)
