"""
Build a native ~1 m canopy-height tileset for Larkana as a single PMTiles file.

The Meta/WRI CHM tiles are already EPSG:3857 (Web Mercator), 32768 px = zoom 17,
so they map straight onto XYZ tiles with no reprojection. We read each source's
Larkana window once (via GDAL range reads - full-file downloads get throttled),
hold it in memory, slice/colour every 256 px tile from it, skip empty tiles, and
pack the lot into app/data/canopy.pmtiles. MapLibre then serves individual tree
crowns crisply on zoom-in.
"""
import os
import io
import math
import time

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
from pmtiles.writer import Writer
from pmtiles.tile import zxy_to_tileid, TileType, Compression

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app", "data")
W, S, E, N = 68.10, 27.47, 68.33, 27.65
CHM_BASE = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/chm/"
QUADKEYS = ["1231202221", "1231202230"]
MINZ, MAXZ = 11, 16   # source read at the x2 overview (~2.4 m); MapLibre overzooms past 16
R = 6378137.0


def ll2m(lon, lat):
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


mminx, mminy = ll2m(W, S)
mmaxx, mmaxy = ll2m(E, N)

# ---- read each source's Larkana window once (native res, range reads) ----
packs = []
for qk in QUADKEYS:
    t = time.time()
    with rasterio.open(CHM_BASE + qk + ".tif") as s:
        win = wfb(mminx, mminy, mmaxx, mmaxy, transform=s.transform)
        win = win.intersection(Window(0, 0, s.width, s.height))
        ow = max(1, int(round(win.width)) // 2)   # x2 overview -> cheap range reads
        oh = max(1, int(round(win.height)) // 2)
        arr = s.read(1, window=win, out_shape=(oh, ow), resampling=Resampling.bilinear).astype(np.float32)
        wt = s.window_transform(win) * Affine.scale(win.width / ow, win.height / oh)
    left, top = wt.c, wt.f
    right = wt.c + wt.a * arr.shape[1]
    bottom = wt.f + wt.e * arr.shape[0]
    packs.append((arr, wt, (left, bottom, right, top)))
    print(f"read {qk}: {arr.shape} in {time.time()-t:.0f}s", flush=True)

# ---- height -> RGBA ramp (matches convert.py) ----
XP, RR, GG, BB = [1, 5, 10, 15], [173, 95, 40, 12], [209, 170, 120, 70], [158, 90, 55, 30]


def colorize(h):
    rgba = np.zeros(h.shape + (4,), np.uint8)
    rgba[..., 0] = np.interp(h, XP, RR).astype(np.uint8)
    rgba[..., 1] = np.interp(h, XP, GG).astype(np.uint8)
    rgba[..., 2] = np.interp(h, XP, BB).astype(np.uint8)
    a = 135 + np.clip((h - 1) / 9.0, 0, 1) * 105
    rgba[..., 3] = np.where(h >= 1.0, a, 0).astype(np.uint8)
    return rgba


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
    r0 = max(0, int(round((iy1 - wt.f) / wt.e)))   # wt.e < 0, top row for iy1(top)
    r1 = min(arr.shape[0], int(round((iy0 - wt.f) / wt.e)))
    if c1 <= c0 or r1 <= r0:
        return None
    piece = arr[r0:r1, c0:c1]
    ow = max(1, int(round(256 * (ix1 - ix0) / (maxx - minx))))
    oh = max(1, int(round(256 * (iy1 - iy0) / (maxy - miny))))
    if piece.shape != (oh, ow):
        piece = np.asarray(Image.fromarray(piece, "F").resize((ow, oh), Image.BILINEAR), np.float32)
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


tiles = {}
for z in range(MINZ, MAXZ + 1):
    x0, x1 = lon2x(W, z), lon2x(E, z)
    y0, y1 = lat2y(N, z), lat2y(S, z)
    cnt = 0
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            h = tile_heights(z, x, y)
            if h is None or not (h >= 1.0).any():
                continue
            im = Image.fromarray(colorize(h), "RGBA")
            buf = io.BytesIO()
            im.save(buf, "PNG")
            tiles[zxy_to_tileid(z, x, y)] = buf.getvalue()
            cnt += 1
    print(f"  z{z}: {cnt} non-empty tiles", flush=True)

print("total tiles:", len(tiles), flush=True)

cx, cy = (W + E) / 2, (S + N) / 2
with open(os.path.join(OUT, "canopy.pmtiles"), "wb") as f:
    w = Writer(f)
    for tid in sorted(tiles):
        w.write_tile(tid, tiles[tid])
    header = {
        "tile_type": TileType.PNG, "tile_compression": Compression.NONE,
        "min_lon_e7": int(W * 1e7), "min_lat_e7": int(S * 1e7),
        "max_lon_e7": int(E * 1e7), "max_lat_e7": int(N * 1e7),
        "center_zoom": 14, "center_lon_e7": int(cx * 1e7), "center_lat_e7": int(cy * 1e7),
    }
    w.finalize(header, {"name": "Larkana canopy height (Meta/WRI CHM v2)",
                        "attribution": "Meta &amp; WRI CHM v2, CC-BY 4.0"})

mb = os.path.getsize(os.path.join(OUT, "canopy.pmtiles")) / 1e6
print(f"wrote app/data/canopy.pmtiles  ({mb:.1f} MB, {len(tiles)} tiles, z{MINZ}-{MAXZ})", flush=True)
