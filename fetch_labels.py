"""Fetch real place / street / landmark names for Larkana from OpenStreetMap
(Overpass API) and write them as GeoJSON for a MapLibre label layer.

Only shows what OSM actually records - no invented names. Coverage of small
colonies and chowks depends on OSM.
"""
import os
import json
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app", "data")
BBOX = "27.47,68.10,27.65,68.33"  # S,W,N,E

QUERY = f"""
[out:json][timeout:120];
(
  node["place"~"city|town|suburb|neighbourhood|quarter|village|locality|hamlet"]({BBOX});
  node["place"="square"]({BBOX});
  node["junction"="yes"]["name"]({BBOX});
  way["highway"~"primary|secondary|tertiary|trunk|residential|unclassified"]["name"]({BBOX});
  node["amenity"~"hospital|clinic|school|college|university|marketplace|bus_station|townhall|police"]["name"]({BBOX});
  node["shop"="mall"]["name"]({BBOX});
  node["leisure"~"park|stadium"]["name"]({BBOX});
);
out center;
"""

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
S = requests.Session()
S.headers.update({
    "User-Agent": "sabzaar-larkana/1.0 (greening map; contact safeer.ali.mirani@gmail.com)",
    "Accept": "application/json",
})

import time
data = None
for url in ENDPOINTS:
    for attempt in range(2):
        try:
            print("querying", url, "attempt", attempt + 1, flush=True)
            r = S.post(url, data={"data": QUERY}, timeout=150)
            if r.status_code == 429:
                print("  429, waiting...", flush=True); time.sleep(8); continue
            r.raise_for_status()
            data = r.json()
            break
        except Exception as ex:
            print("  failed:", str(ex)[:90], flush=True); time.sleep(3)
    if data:
        break

if not data:
    raise SystemExit("Overpass unreachable")

seen = set()
feats = []
for e in data.get("elements", []):
    tags = e.get("tags", {})
    name = tags.get("name")
    if not name:
        continue
    if e["type"] == "node":
        lon, lat = e.get("lon"), e.get("lat")
    else:
        c = e.get("center") or {}
        lon, lat = c.get("lon"), c.get("lat")
    if lon is None or lat is None:
        continue
    key = (name, round(lon, 4), round(lat, 4))
    if key in seen:
        continue
    seen.add(key)
    if tags.get("place"):
        kind, place = "place", tags["place"]
    elif tags.get("highway"):
        kind, place = "road", tags["highway"]
    else:
        kind = "poi"
        place = tags.get("amenity") or tags.get("leisure") or tags.get("shop") or "poi"
    feats.append({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        "properties": {"name": name, "kind": kind, "sub": place,
                       "name_en": tags.get("name:en", ""), "name_ur": tags.get("name:ur", "")},
    })

gj = {"type": "FeatureCollection", "features": feats}
with open(os.path.join(OUT, "labels.geojson"), "w", encoding="utf-8") as f:
    json.dump(gj, f, ensure_ascii=False)

kinds = {}
for x in feats:
    kinds[x["properties"]["kind"]] = kinds.get(x["properties"]["kind"], 0) + 1
print("labels:", len(feats), kinds, flush=True)
