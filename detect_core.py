"""
Run the current-tree detector across Larkana's URBAN CORE in blocks (bounded
memory, one model load). Same method as detect_trees.py: DeepForest crowns on
Esri ~0.6 m, confirmed by greenness and filtered off building roofs.

Output: app/data/current_trees.geojson  (all detected current trees in the core)
Progress is printed per block so the run can be monitored.
"""
import os, math, time, json, urllib.request
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app", "data")
CACHE = os.path.join(HERE, "_esri_sample")
os.makedirs(CACHE, exist_ok=True); os.makedirs(OUT, exist_ok=True)

# ---- area of interest: defaults to Larkana's dense urban core; override via env
# for the full district on HPC (AOI_W/S/E/N, BLOCK, UP, OUTFILE). ----
W = float(os.environ.get("AOI_W", 68.192))
S = float(os.environ.get("AOI_S", 27.549))
E = float(os.environ.get("AOI_E", 68.228))
N = float(os.environ.get("AOI_N", 27.575))
Z = int(os.environ.get("AOI_Z", 18))     # Esri ~0.6 m/px at z18
UP = int(os.environ.get("UP", 2))        # upsample so DeepForest sees larger crowns
PATCH = 500
SCORE = float(os.environ.get("SCORE", 0.20))
GREEN_MIN = 0.35
BLOCK = int(os.environ.get("BLOCK", 8))  # tiles per block side; keeps memory small
OUTFILE = os.environ.get("OUTFILE", os.path.join(OUT, "current_trees.geojson"))
ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
UA = {"User-Agent": "Mozilla/5.0 (civic tree research)"}
R = 6378137.0

def lon2tx(lon, z): return int(math.floor((lon + 180) / 360 * 2**z))
def lat2ty(lat, z):
    la = math.radians(lat); return int(math.floor((1 - math.asinh(math.tan(la)) / math.pi) / 2 * 2**z))
def tx2lon(x, z): return x / 2**z * 360 - 180
def ty2lat(y, z):
    n = math.pi - 2 * math.pi * y / 2**z; return math.degrees(math.atan(math.sinh(n)))
def merc_x(lon): return R * math.radians(lon)
def merc_y(lat): return R * math.log(math.tan(math.pi/4 + math.radians(lat)/2))
def inv_lon(x): return math.degrees(x / R)
def inv_lat(y): return math.degrees(2*math.atan(math.exp(y/R)) - math.pi/2)

x0, x1 = lon2tx(W, Z), lon2tx(E, Z)
y0, y1 = lat2ty(N, Z), lat2ty(S, Z)
print(f"core tiles: x {x0}-{x1} ({x1-x0+1}), y {y0}-{y1} ({y1-y0+1}) = {(x1-x0+1)*(y1-y0+1)} tiles", flush=True)

# FETCH_ONLY: just download+cache all Esri tiles for the AOI, then exit. Run this on a
# machine WITH internet (your PC, or an HPC login node) so the GPU compute node - which
# usually has no outbound network - can read the cache offline.
if os.environ.get("FETCH_ONLY"):
    import urllib.request as _u
    tot = (x1 - x0 + 1) * (y1 - y0 + 1); n = 0
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            fp = os.path.join(CACHE, f"{Z}_{x}_{y}.jpg")
            if not os.path.exists(fp) or os.path.getsize(fp) < 500:
                for _ in range(4):
                    try:
                        d = _u.urlopen(_u.Request(ESRI.format(z=Z, y=y, x=x), headers=UA), timeout=30).read()
                        open(fp, "wb").write(d); break
                    except Exception:
                        time.sleep(1.0)
            n += 1
            if n % 200 == 0: print(f"fetched {n}/{tot}", flush=True)
    print(f"FETCH_ONLY done: {n} tiles cached in {CACHE}", flush=True)
    raise SystemExit(0)

# ---- load model + buildings once ----
import inspect
from deepforest import main as df_main
m = df_main.deepforest()
try:
    m.load_model("weecology/deepforest-tree")
except TypeError:
    m.load_model(model_name="weecology/deepforest-tree")
pp = inspect.signature(m.predict_tile).parameters
import torch
try: m.config["workers"] = 0        # 0 = load data in-process; avoids Windows DataLoader deadlock
except Exception: pass
print(f"model ready | CUDA available: {torch.cuda.is_available()} | workers=0", flush=True)

import geopandas as gpd
from shapely.geometry import Point
bld = gpd.read_file(os.path.join(HERE, "_buildings.geojson"), bbox=(W, S, E, N))
bld = bld[bld.geometry.notna()].reset_index(drop=True); bsindex = bld.sindex
print(f"buildings in core: {len(bld)}", flush=True)

def fetch(x, y):
    fp = os.path.join(CACHE, f"{Z}_{x}_{y}.jpg")
    if not os.path.exists(fp) or os.path.getsize(fp) < 500:
        for _ in range(4):
            try:
                d = urllib.request.urlopen(urllib.request.Request(ESRI.format(z=Z, y=y, x=x), headers=UA), timeout=30).read()
                open(fp, "wb").write(d); break
            except Exception:
                time.sleep(1.0)
    return fp

def predict(arr, tmp):
    kw = {}
    if "patch_size" in pp: kw["patch_size"] = PATCH
    if "patch_overlap" in pp: kw["patch_overlap"] = 0.25
    if "workers" in pp: kw["workers"] = 0
    if "image" in pp: return m.predict_tile(image=arr, **kw)
    Image.fromarray(arr).save(tmp)
    if "raster_path" in pp: return m.predict_tile(raster_path=tmp, **kw)
    if "path" in pp: return m.predict_tile(path=tmp, **kw)
    return m.predict_tile(tmp, **kw)

def on_building(lon, lat):
    p = Point(lon, lat)
    for k in bsindex.query(p):
        if bld.geometry.iloc[k].contains(p): return True
    return False

feats = []
nb = 0
tmp = os.path.join(CACHE, "_block.png")
for bx in range(x0, x1 + 1, BLOCK):
    for by in range(y0, y1 + 1, BLOCK):
        bxe, bye = min(bx + BLOCK - 1, x1), min(by + BLOCK - 1, y1)
        nx, ny = bxe - bx + 1, bye - by + 1
        mosaic = Image.new("RGB", (nx*256, ny*256))
        for i, x in enumerate(range(bx, bxe + 1)):
            for j, y in enumerate(range(by, bye + 1)):
                try: mosaic.paste(Image.open(fetch(x, y)).convert("RGB"), (i*256, j*256))
                except Exception: pass
        MW, MH = mosaic.size
        mx0, my0 = merc_x(tx2lon(bx, Z)), merc_y(ty2lat(by, Z))
        mx1, my1 = merc_x(tx2lon(bxe+1, Z)), merc_y(ty2lat(bye+1, Z))
        arr = np.array(mosaic.resize((MW*UP, MH*UP), Image.BILINEAR))
        nb += 1
        try:
            boxes = predict(arr, tmp)
        except Exception as e:
            print(f"block {nb} predict failed: {e}", flush=True); continue
        if boxes is None or not len(boxes):
            print(f"block {nb}/{bx},{by}: 0 (total {len(feats)})", flush=True); continue
        boxes = boxes[boxes.score >= SCORE]
        mos = np.asarray(mosaic).astype(np.float32)
        added = 0
        for _, b in boxes.iterrows():
            xa, xb = max(0, int(b.xmin/UP)), min(MW, int(b.xmax/UP))
            ya, yb = max(0, int(b.ymin/UP)), min(MH, int(b.ymax/UP))
            if xb <= xa or yb <= ya: continue
            c = mos[ya:yb, xa:xb]; Rr, Gg, Bb = c[..., 0], c[..., 1], c[..., 2]
            if ((Gg > Rr*1.02) & (Gg > Bb*1.02)).mean() < GREEN_MIN: continue
            cx = (b.xmin + b.xmax) / 2 / UP; cy = (b.ymin + b.ymax) / 2 / UP
            lon = inv_lon(mx0 + (mx1 - mx0) * (cx / MW)); lat = inv_lat(my0 + (my1 - my0) * (cy / MH))
            if on_building(lon, lat): continue
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                          "properties": {"score": round(float(b.score), 3)}})
            added += 1
        print(f"block {nb}/{bx},{by}: +{added} (total {len(feats)})", flush=True)
        json.dump({"type": "FeatureCollection", "features": feats}, open(OUTFILE, "w"))   # persist after each block

json.dump({"type": "FeatureCollection", "features": feats}, open(OUTFILE, "w"))
print(f"DONE: {len(feats)} current trees -> {OUTFILE}", flush=True)
