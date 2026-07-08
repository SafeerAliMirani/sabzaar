"""
Build a crisp, deep-zoomable canopy-height overlay for Larkana as a standard XYZ
raster-tile pyramid (app/data/canopy_tiles/{z}/{x}/{y}.png). No PMTiles, no custom
protocol - MapLibre serves it with a plain raster source, so it can't break the
style load. Downsampling is done with MAX-POOLING in height space so an isolated
tree never gets averaged away, and colour is applied after, with a hard alpha cut
below ~1.5 m so bare gaps show through.

The Meta/WRI CHM tiles are already EPSG:3857 (Web Mercator). We read each source's
Larkana window once (GDAL range reads at the x2 overview ~2.4 m; full-native reads
get throttled by S3), hold it in memory, and slice every 256 px tile from it.
"""
import os
import math
import time
import shutil

os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "EMPTY_DIR"
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")
import numpy as np
import rasterio
from rasterio import Affine
from rasterio.enums import Resampling
from rasterio.windows import from_bounds as wfb, Window
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app", "data")
TILES = os.path.join(OUT, "canopy_tiles")
W, S, E, N = 68.10, 27.47, 68.33, 27.65
CHM_BASE = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/chm/"
QUADKEYS = ["1231202221", "1231202230"]
MINZ, MAXZ = 11, 17    # native tiles to z17 (~1.2 m) - tight to actual tree crowns
TREE_MIN = 1.5         # metres: hard alpha cut - below this is not drawn
R = 6378137.0
CACHE = os.path.join(HERE, "_cog")
os.makedirs(CACHE, exist_ok=True)


def download_cog(url, path):
    """S3 throttles full-file GETs but not byte ranges, so fetch in chunks."""
    if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
        print(f"cached {os.path.basename(path)}", flush=True)
        return
    import requests
    total = int(requests.head(url, timeout=30).headers["Content-Length"])
    print(f"downloading {os.path.basename(path)} ({total//1048576} MB) in chunks...", flush=True)
    step = 4 * 1024 * 1024
    with open(path, "wb") as f:
        pos = 0
        while pos < total:
            end = min(pos + step - 1, total - 1)
            for attempt in range(4):
                try:
                    r = requests.get(url, headers={"Range": f"bytes={pos}-{end}"}, timeout=90)
                    if r.status_code in (200, 206) and r.content:
                        f.write(r.content)
                        break
                except Exception:
                    time.sleep(2)
            else:
                raise RuntimeError("chunk failed at " + str(pos))
            pos = end + 1
    print(f"  done {os.path.basename(path)}", flush=True)


def ll2m(lon, lat):
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


mminx, mminy = ll2m(W, S)
mmaxx, mmaxy = ll2m(E, N)

# ---- read each source's Larkana window once (x2 overview -> cheap range reads) ----
packs = []
for qk in QUADKEYS:
    t = time.time()
    with rasterio.open(CHM_BASE + qk + ".tif") as s:
        win = wfb(mminx, mminy, mmaxx, mmaxy, transform=s.transform)
        win = win.intersection(Window(0, 0, s.width, s.height))
        ow = max(1, int(round(win.width)) // 2)
        oh = max(1, int(round(win.height)) // 2)
        arr = s.read(1, window=win, out_shape=(oh, ow), resampling=Resampling.bilinear).astype(np.float32)
        wt = s.window_transform(win) * Affine.scale(win.width / ow, win.height / oh)
    left, top = wt.c, wt.f
    right, bottom = wt.c + wt.a * arr.shape[1], wt.f + wt.e * arr.shape[0]
    packs.append((arr, wt, (left, bottom, right, top)))
    print(f"read {qk}: {arr.shape} in {time.time()-t:.0f}s", flush=True)

# ---- colour ramp (height -> RGBA), hard alpha below TREE_MIN ----
XP, RR, GG, BB = [1.5, 5, 10, 15], [173, 95, 40, 12], [209, 170, 120, 70], [158, 90, 55, 30]


def colorize(h):
    rgba = np.zeros(h.shape + (4,), np.uint8)
    rgba[..., 0] = np.interp(h, XP, RR).astype(np.uint8)
    rgba[..., 1] = np.interp(h, XP, GG).astype(np.uint8)
    rgba[..., 2] = np.interp(h, XP, BB).astype(np.uint8)
    rgba[..., 3] = np.where(h >= TREE_MIN, 235, 0).astype(np.uint8)
    return rgba


def resize2d(a, oh, ow):
    """Resize a 2D height array. Downsampling uses MAX-POOLING so single trees
    survive; upsampling uses nearest so tree pixels stay sharp."""
    h, w = a.shape
    if h == oh and w == ow:
        return a
    if h >= oh and w >= ow:                       # downscale: two-pass max-pool
        yb = np.minimum(np.arange(h) * oh // h, oh - 1)
        xb = np.minimum(np.arange(w) * ow // w, ow - 1)
        tmp = np.zeros((oh, w), np.float32)
        np.maximum.at(tmp, yb, a)
        out = np.zeros((oh, ow), np.float32)
        np.maximum.at(out.T, xb, tmp.T)
        return out
    yi = np.minimum(np.arange(oh) * h // oh, h - 1)  # upscale: nearest
    xi = np.minimum(np.arange(ow) * w // ow, w - 1)
    return a[yi][:, xi]


def tile_merc_bounds(z, x, y):
    span = 2 * math.pi * R / (2 ** z)
    minx = -math.pi * R + x * span
    maxy = math.pi * R - y * span
    return minx, maxy - span, minx + span, maxy


def lon2x(lon, z):
    return int(math.floor((lon + 180.0) / 360.0 * 2 ** z))


def lat2y(lat, z):
    la = math.radians(lat)
    return int(math.floor((1 - math.asinh(math.tan(la)) / math.pi) / 2 * 2 ** z))


def sub_from(pack, minx, miny, maxx, maxy):
    arr, wt, (bl, bb, br, bt) = pack
    ix0, iy0 = max(minx, bl), max(miny, bb)
    ix1, iy1 = min(maxx, br), min(maxy, bt)
    if ix0 >= ix1 or iy0 >= iy1:
        return None
    c0 = max(0, int(round((ix0 - wt.c) / wt.a)))
    c1 = min(arr.shape[1], int(round((ix1 - wt.c) / wt.a)))
    r0 = max(0, int(round((iy1 - wt.f) / wt.e)))
    r1 = min(arr.shape[0], int(round((iy0 - wt.f) / wt.e)))
    if c1 <= c0 or r1 <= r0:
        return None
    piece = arr[r0:r1, c0:c1]
    ow = max(1, int(round(256 * (ix1 - ix0) / (maxx - minx))))
    oh = max(1, int(round(256 * (iy1 - iy0) / (maxy - miny))))
    piece = resize2d(piece, oh, ow)
    col = int(round(256 * (ix0 - minx) / (maxx - minx)))
    row = int(round(256 * (maxy - iy1) / (maxy - miny)))
    return piece, row, col


def tile_heights(z, x, y):
    minx, miny, maxx, maxy = tile_merc_bounds(z, x, y)
    out = np.zeros((256, 256), np.float32)
    got = False
    for pk in packs:
        r = sub_from(pk, minx, miny, maxx, maxy)
        if r is None:
            continue
        piece, row, col = r
        h = min(piece.shape[0], 256 - row)
        w = min(piece.shape[1], 256 - col)
        if h <= 0 or w <= 0:
            continue
        out[row:row + h, col:col + w] = np.maximum(out[row:row + h, col:col + w], piece[:h, :w])
        got = True
    return out if got else None


# ---- write the XYZ pyramid ----
if os.path.exists(TILES):
    shutil.rmtree(TILES)
os.makedirs(TILES, exist_ok=True)

total = 0
for z in range(MINZ, MAXZ + 1):
    x0, x1 = lon2x(W, z), lon2x(E, z)
    y0, y1 = lat2y(N, z), lat2y(S, z)
    cnt = 0
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            h = tile_heights(z, x, y)
            if h is None or not (h >= TREE_MIN).any():
                continue
            d = os.path.join(TILES, str(z), str(x))
            os.makedirs(d, exist_ok=True)
            Image.fromarray(colorize(h), "RGBA").save(os.path.join(d, f"{y}.png"), optimize=True)
            cnt += 1
    total += cnt
    print(f"  z{z}: {cnt} tiles", flush=True)

# a small manifest the app can read for min/max zoom + bounds
import json
with open(os.path.join(OUT, "canopy_tiles.json"), "w") as f:
    json.dump({"minzoom": MINZ, "maxzoom": MAXZ, "bounds": [W, S, E, N], "tiles": total,
               "note": f"native to ~1.2 m (z{MAXZ}); max-pooled so single trees survive"}, f)

size_mb = sum(os.path.getsize(os.path.join(dp, fn)) for dp, _, fns in os.walk(TILES) for fn in fns) / 1e6
print(f"wrote {total} tiles to app/data/canopy_tiles/ ({size_mb:.1f} MB, z{MINZ}-{MAXZ})", flush=True)
