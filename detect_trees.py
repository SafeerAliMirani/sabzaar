"""
Detect CURRENT individual trees in Larkana's urban core from present-day
high-resolution satellite imagery, using DeepForest (a pretrained tree-crown
detector, RetinaNet on NEON data). This complements the canopy-HEIGHT layer,
which is good at "is this tall vegetation" but is a few years old, with today's
actual tree positions.

Imagery: Esri World Imagery (~0.6 m at z18). Used here only to DERIVE tree
points for non-commercial civic research - we publish the points, not the
imagery. Swap ESRI for an institutional source (Maxar/Planet) for sharper crowns.

Output:
  app/data/current_trees.geojson  - detected tree points (lon, lat, score)
  detect_preview.png              - imagery + boxes, for a visual quality check
"""
import os, math, time, json, urllib.request
import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app", "data")
CACHE = os.path.join(HERE, "_esri_sample")
os.makedirs(CACHE, exist_ok=True); os.makedirs(OUT, exist_ok=True)

# ---- AOI: defaults to central Larkana; override via env (AOI_W/S/E/N) ----
W = float(os.environ.get("AOI_W", 68.200))
S = float(os.environ.get("AOI_S", 27.553))
E = float(os.environ.get("AOI_E", 68.219))
N = float(os.environ.get("AOI_N", 27.568))
Z = 18                 # Esri max real zoom for Larkana (~0.6 m/px)
UP = 2                 # upsample: 0.6 m -> ~0.3 m so DeepForest sees bigger crowns
PATCH = 500            # predict patch size (px, in upsampled space)
SCORE = 0.20           # keep detections above this confidence
GREEN_MIN = 0.35       # box must be >=35% green-dominant pixels (kills rooftop false positives)
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

# ---- fetch + stitch Esri tiles over the AOI ----
x0, x1 = lon2tx(W, Z), lon2tx(E, Z)
y0, y1 = lat2ty(N, Z), lat2ty(S, Z)      # y increases southward
nx, ny = x1 - x0 + 1, y1 - y0 + 1
print(f"AOI: {nx}x{ny} = {nx*ny} Esri tiles at z{Z}", flush=True)
mosaic = Image.new("RGB", (nx*256, ny*256))
small = 0
for i, x in enumerate(range(x0, x1+1)):
    for j, y in enumerate(range(y0, y1+1)):
        fp = os.path.join(CACHE, f"{Z}_{x}_{y}.jpg")
        if not os.path.exists(fp) or os.path.getsize(fp) < 500:
            for _ in range(4):
                try:
                    data = urllib.request.urlopen(urllib.request.Request(ESRI.format(z=Z, x=x, y=y), headers=UA), timeout=30).read()
                    open(fp, "wb").write(data); break
                except Exception:
                    time.sleep(1.5)
        try:
            if os.path.getsize(fp) < 3000: small += 1
            mosaic.paste(Image.open(fp).convert("RGB"), (i*256, j*256))
        except Exception as e:
            print("tile skip", x, y, e, flush=True)
if small: print(f"WARNING: {small} tiles look like 'no-data' placeholders (<3 KB)", flush=True)
print("mosaic", mosaic.size, flush=True)

mx0, my0 = merc_x(tx2lon(x0, Z)), merc_y(ty2lat(y0, Z))       # NW corner
mx1, my1 = merc_x(tx2lon(x1+1, Z)), merc_y(ty2lat(y1+1, Z))   # SE corner
MW, MH = mosaic.size
big = mosaic.resize((MW*UP, MH*UP), Image.BILINEAR)
arr = np.array(big)

# ---- DeepForest ----
print("loading DeepForest...", flush=True)
import inspect
from deepforest import main as df_main
m = df_main.deepforest()
try:
    m.load_model("weecology/deepforest-tree")
except TypeError:
    m.load_model(model_name="weecology/deepforest-tree")
print("predict_tile...", flush=True)
tmp = os.path.join(CACHE, "_mosaic.png"); big.save(tmp)
params = inspect.signature(m.predict_tile).parameters
kw = {}
if "patch_size" in params: kw["patch_size"] = PATCH
if "patch_overlap" in params: kw["patch_overlap"] = 0.25
if "image" in params:                       # array-based API (older)
    boxes = m.predict_tile(image=arr, **kw)
elif "raster_path" in params:               # path-based API (older)
    boxes = m.predict_tile(raster_path=tmp, **kw)
elif "path" in params:                       # path-based API (2.x)
    boxes = m.predict_tile(path=tmp, **kw)
else:
    boxes = m.predict_tile(tmp, **kw)

# ---- confirm each crown: must be GREEN and NOT sitting on a building roof ----
mos = np.asarray(mosaic).astype(np.float32)         # original-res RGB
def green_frac(b):
    xa, xb = max(0, int(b.xmin/UP)), min(MW, int(b.xmax/UP))
    ya, yb = max(0, int(b.ymin/UP)), min(MH, int(b.ymax/UP))
    if xb <= xa or yb <= ya: return 0.0
    c = mos[ya:yb, xa:xb]; R, G, B = c[..., 0], c[..., 1], c[..., 2]
    return float(((G > R*1.02) & (G > B*1.02)).mean())    # green-dominant = vegetation

import geopandas as gpd
from shapely.geometry import Point
try:
    bld = gpd.read_file(os.path.join(HERE, "_buildings.geojson"), bbox=(W, S, E, N))
    bld = bld[bld.geometry.notna()].reset_index(drop=True); bsindex = bld.sindex
    print(f"buildings in AOI: {len(bld)}", flush=True)
except Exception as e:
    print("no building mask:", e, flush=True); bld = None; bsindex = None
def on_building(lon, lat):
    if bld is None: return False
    p = Point(lon, lat)
    for i in bsindex.query(p):
        if bld.geometry.iloc[i].contains(p): return True
    return False

def centroid_lonlat(b):
    cx = (b.xmin + b.xmax) / 2 / UP; cy = (b.ymin + b.ymax) / 2 / UP
    return inv_lon(mx0 + (mx1 - mx0) * (cx / MW)), inv_lat(my0 + (my1 - my0) * (cy / MH))

kept, rej = [], []
if boxes is not None and len(boxes):
    boxes = boxes[boxes.score >= SCORE].reset_index(drop=True)
    for _, b in boxes.iterrows():
        lon, lat = centroid_lonlat(b)
        ok = (green_frac(b) >= GREEN_MIN) and (not on_building(lon, lat))
        (kept if ok else rej).append((b, lon, lat))
print(f"score>= {SCORE}: {len(kept)+len(rej)} boxes | trees kept: {len(kept)} | dropped (non-green or on roof): {len(rej)}", flush=True)

feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
          "properties": {"score": round(float(b.score), 3)}} for (b, lon, lat) in kept]
json.dump({"type": "FeatureCollection", "features": feats},
          open(os.path.join(OUT, "current_trees.geojson"), "w"))
print("wrote app/data/current_trees.geojson:", len(feats), "trees", flush=True)

# ---- preview: green = kept tree, red = dropped ----
prev = mosaic.copy(); d = ImageDraw.Draw(prev)
for (b, _, _) in rej:
    d.rectangle([b.xmin/UP, b.ymin/UP, b.xmax/UP, b.ymax/UP], outline=(255, 60, 60), width=1)
for (b, _, _) in kept:
    d.rectangle([b.xmin/UP, b.ymin/UP, b.xmax/UP, b.ymax/UP], outline=(0, 255, 120), width=2)
if max(prev.size) > 1600:
    s = 1600 / max(prev.size); prev = prev.resize((int(prev.size[0]*s), int(prev.size[1]*s)))
prev.save(os.path.join(HERE, "detect_preview.png"))
print("wrote detect_preview.png", flush=True)
