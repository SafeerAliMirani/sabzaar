# Sabzaar data sources (de-risked 2026-07-07)

Every URL below was tested live on this machine on 2026-07-07 (HTTP status + range-read checks via PowerShell/curl). The data path works end to end.

## Geography

- Larkana center: 27.5565 N, 68.2102 E (OSM Nominatim)
- Clip bbox (city + surrounds, ~23 x 20 km): `[68.10, 27.47, 68.33, 27.65]` (lonW, latS, lonE, latN)
- Map default: center `[68.2102, 27.5565]`, zoom ~13

## 1. Canopy height, ~1 m: Meta + WRI CHM v2 (primary canopy layer)

- Bucket: `s3://dataforgood-fb-data/forests/v2/global/dinov3_global_chm_v2_ml3/chm/` (us-east-1, public, no auth)
- Tiles are zoom-10 quadkeys. Larkana needs two:
  - `1231202221.tif` (west half of city) - verified 200, 29.1 MB
  - `1231202230.tif` (east half) - verified 200, 66.8 MB
  - HTTP URL pattern: `https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/chm/<quadkey>.tif`
- COG, range reads confirmed (206) so windowed clips download only what we need
- Licence: CC-BY 4.0. Cite: "Meta and World Resources Institute, 2026. Version 2 High Resolution Canopy Height Maps (CHMv2). Source imagery (c) 2016 Vantor." Registry: https://registry.opendata.aws/dataforgood-fb-forestsv2/
- Resolution stated ~1 m; confirm exact pixel size from the tile metadata during build, do not overclaim

## 2. Land cover, 10 m: ESA WorldCover v200 (2021) (green vs bare context)

- File: `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N27E066_Map.tif`
- Verified 200 (74.5 MB), range reads confirmed (206). Single tile covers the whole bbox (tile spans 66-69 E, 27-30 N)
- 11 classes; we care about 10 (tree cover), 20 (shrub), 30 (grass), 40 (cropland), 50 (built-up), 80 (water)
- Licence: CC-BY 4.0. Cite Zanaga et al. 2022, DOI 10.5281/zenodo.7254221
- Caveat: map year is 2021; label it as such in the UI

## 3. Heat (land surface temperature): Landsat Collection 2 Level-2, ST_B10

- Via Microsoft Planetary Computer STAC (free, anonymous + SAS signing, both verified):
  - Search: `POST https://planetarycomputer.microsoft.com/api/stac/v1/search` collections `landsat-c2-l2`, our bbox
  - Sign asset href: `GET https://planetarycomputer.microsoft.com/api/sas/v1/sign?href=<url>`
- Larkana is path 152 row 041. Found 0.0% cloud scenes:
  - `LC08_L2SP_152041_20260612_02_T1` (2026-06-12, peak summer - ideal)
  - `LC09_L2SP_152041_20260604_02_T1` (2026-06-04)
- Asset `lwir11` (ST_B10): Kelvin = DN * 0.00341802 + 149.0; 30 m grid (resampled from 100 m thermal). Signed HEAD 200 (81.8 MB full scene), range reads 206, so a windowed clip is a few MB
- Licence: public domain (courtesy USGS/NASA)
- Honest framing: this is surface temperature on one clear day, not air temperature and not a climate average. Label the date

## 4. Basemap tiles (all three verified serving Larkana at z12)

- OSM raster: `https://tile.openstreetmap.org/{z}/{x}/{y}.png` - attribution "(c) OpenStreetMap contributors"; tile policy allows light use, fine for MVP
- Carto Voyager: `https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png` - attribution OSM + CARTO; free for non-commercial
- Esri World Imagery (satellite look): `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}` - attribution "Esri, Maxar, Earthstar Geographics"; check Esri terms before launch
- Recommendation: Carto or OSM as default, Esri imagery as an optional toggle. Re-check each policy at deploy

## Pipeline plan (convert.py, offline, same pattern as the other projects)

- Machine ready: Python 3.12.10 + pip 25.2 confirmed on this PC
- `pip install rasterio` (wheels exist for 3.12 on Windows), optionally numpy + pillow
- Steps: windowed-read the two CHM quadkey tiles + WorldCover + signed LST for the bbox, reproject/merge, export web-ready outputs (PNG overlays + bounds JSON, or small GeoJSON), keep raw values honest (heights in m, LST in C with capture date)
- Output goes into the app as static files; no backend

## Verdict

De-risk passed. Real canopy (1 m), real land cover (10 m), real heat (30 m, 0% cloud, June 2026) and a free basemap all reach the browser with no accounts, no tokens, no cost. Phase 1 is buildable exactly as briefed.
