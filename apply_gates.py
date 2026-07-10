"""
Multi-modal gate on the detected-tree points (Fable-style): keep a detection only
if it is BOTH tall (canopy-height model) and vegetated (Sentinel-2 NDVI). This drops
false positives on grass, low shrubs, dry ground and shadows that DeepForest picked
up from RGB texture alone. Filters app/data/current_trees.geojson (keeps a .prefilter
backup). Runs on a machine WITH internet (the PC).

- Height: Meta/WRI CHM (~1 m, EPSG:3857) read as a ~5 m grid over the points' bbox.
- NDVI:   Sentinel-2 L2A (10 m) latest low-cloud scene via Microsoft Planetary Computer.
Both gates are SOFT: a point is only dropped where the layer actually covers it and
reads clearly low; missing/nodata cover -> keep (benefit of the doubt). If the NDVI
packages/scene are unavailable it falls back to height-only, which is the main filter.
"""
import os, math, json, shutil
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
import numpy as np
import rasterio
from rasterio import Affine
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import from_bounds as wfb, Window
from rasterio.transform import from_bounds as t_from_bounds

HERE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(HERE, "app", "data")
GEO = os.path.join(OUT, "current_trees.geojson")
HEIGHT_MIN = float(os.environ.get("HEIGHT_MIN", 1.5))   # m: below this (where CHM covers) = not a tree
NDVI_MIN   = float(os.environ.get("NDVI_MIN", 0.12))    # below this (where S2 covers) = not vegetated
CHM_BASE = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/chm/"
QUADKEYS = ["1231202221", "1231202230"]

feats = json.load(open(GEO))["features"]
lons = np.array([f["geometry"]["coordinates"][0] for f in feats])
lats = np.array([f["geometry"]["coordinates"][1] for f in feats])
W, S, E, N = lons.min()-0.002, lats.min()-0.002, lons.max()+0.002, lats.max()+0.002
print(f"{len(feats)} points; bbox {W:.3f},{S:.3f},{E:.3f},{N:.3f}", flush=True)

mlat = (S+N)/2
GW = int(round((E-W)*111320*math.cos(math.radians(mlat))/5)); GH = int(round((N-S)*110570/5))
TF = t_from_bounds(W, S, E, N, GW, GH)
print(f"gate grid {GW}x{GH} @ ~5 m", flush=True)


def read_grid(url, resampling=Resampling.max, band=1):
    dst = np.full((GH, GW), np.nan, np.float32)
    with rasterio.open(url) as s:
        epsg = s.crs.to_epsg()
        l, b, r, t = transform_bounds("EPSG:4326", s.crs, W, S, E, N, densify_pts=21) if epsg != 4326 else (W, S, E, N)
        win = wfb(l, b, r, t, transform=s.transform)
        try: win = win.intersection(Window(0, 0, s.width, s.height))
        except Exception: return dst
        if win.width < 1 or win.height < 1: return dst
        rw = max(1, min(int(round(win.width)), GW*2)); rh = max(1, min(int(round(win.height)), GH*2))
        arr = s.read(band, window=win, out_shape=(rh, rw), resampling=Resampling.average, masked=True).astype("float32").filled(np.nan)
        rt = s.window_transform(win) * Affine.scale(win.width/rw, win.height/rh)
        reproject(arr, dst, src_transform=rt, src_crs=s.crs, dst_transform=TF, dst_crs="EPSG:4326",
                  resampling=resampling, src_nodata=np.nan, dst_nodata=np.nan)
    return dst


print("reading CHM height...", flush=True)
chm = np.full((GH, GW), np.nan, np.float32)
for qk in QUADKEYS:
    g = read_grid(CHM_BASE + qk + ".tif")
    m = np.isfinite(g); chm[m] = np.fmax(np.nan_to_num(chm[m], nan=0.0), g[m])
print(f"  CHM covers {np.isfinite(chm).mean()*100:.0f}% of bbox, max {np.nanmax(chm):.1f} m", flush=True)

print("fetching Sentinel-2 NDVI...", flush=True)
ndvi = None
try:
    import planetary_computer as pc, pystac_client
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
    items = list(cat.search(collections=["sentinel-2-l2a"], bbox=[W, S, E, N],
                            query={"eo:cloud_cover": {"lt": 15}},
                            sortby=[{"field": "properties.eo:cloud_cover", "direction": "asc"}], limit=1).items())
    if items:
        it = items[0]
        print(f"  scene {it.id} ({it.properties.get('eo:cloud_cover')}% cloud, {it.properties.get('datetime')})", flush=True)
        def rd(a):
            with rasterio.open(it.assets[a].href) as s:
                l, b, r, t = transform_bounds("EPSG:4326", s.crs, W, S, E, N, densify_pts=21)
                win = wfb(l, b, r, t, transform=s.transform).intersection(Window(0, 0, s.width, s.height))
                arr = s.read(1, window=win).astype("float32")
                d = np.full((GH, GW), np.nan, np.float32)
                reproject(arr, d, src_transform=s.window_transform(win), src_crs=s.crs,
                          dst_transform=TF, dst_crs="EPSG:4326", resampling=Resampling.bilinear)
                return d
        nir, red = rd("B08"), rd("B04")
        ndvi = (nir - red) / (nir + red + 1e-6)
        print(f"  NDVI median {np.nanmedian(ndvi):.2f}", flush=True)
except Exception as e:
    print("  NDVI unavailable, height-only gate:", e, flush=True)


def gx(lon): return min(GW-1, max(0, int((lon-W)/(E-W)*GW)))
def gy(lat): return min(GH-1, max(0, int((N-lat)/(N-S)*GH)))
def smax(grid, ix, iy, r=1):     # max over a 3x3 window - robust to a cell of misalignment
    sub = grid[max(0, iy-r):iy+r+1, max(0, ix-r):ix+r+1]
    return np.nanmax(sub) if np.isfinite(sub).any() else np.nan


kept = []; drop_h = drop_n = 0
for f, lo, la in zip(feats, lons, lats):
    ix, iy = gx(lo), gy(la)
    h = smax(chm, ix, iy)
    if np.isfinite(h) and h < HEIGHT_MIN:
        drop_h += 1; continue
    if ndvi is not None:
        nd = smax(ndvi, ix, iy)
        if np.isfinite(nd) and nd < NDVI_MIN:
            drop_n += 1; continue
    kept.append(f)

print(f"KEPT {len(kept)} / {len(feats)}  (dropped {drop_h} too-short + {drop_n} non-vegetated)", flush=True)
shutil.copy(GEO, GEO + ".prefilter")
json.dump({"type": "FeatureCollection", "features": kept}, open(GEO, "w"))
print("wrote filtered current_trees.geojson (backup: current_trees.geojson.prefilter)", flush=True)
