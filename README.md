# Sabzaar سبزار

A map of Larkana's real tree canopy and summer heat, with a guide to planting the right native shade trees. Built to help green the city and cut its extreme summer heat.

Live: _to be deployed_

## What it shows

Three measured satellite layers over Larkana, Sindh, plus editorial planting guidance, kept visually and textually separate so nothing is overclaimed.

- **Tree canopy height** - Meta and WRI High-Resolution Canopy Height v2 (about 1 m), CC-BY 4.0. Shows where trees are and how tall, not species or age.
- **Land cover** - ESA WorldCover 2021 (10 m), CC-BY 4.0. An independent cross-check of the greenery picture.
- **Land surface temperature** - Landsat Collection 2 Level-2 thermal band (30 m), public domain, via Microsoft Planetary Computer. Ground temperature on one clear day (12 June 2026, 0% cloud), not air temperature and not a climate average.
- **Plant the right tree** - a curated list of native, heat-tolerant shade trees for Larkana, and why new Conocarpus planting is a poor trade.

The headline numbers for the mapped area: about 5.5% under tree canopy (satellite, 3 m and taller), 7% tree cover on the independent 10 m map, and a mean ground temperature near 50°C with surfaces up to 61°C on the June scene.

## Honesty tiers

1. Measured satellite data (canopy, land cover, heat).
2. Editorial guidance (which and where to plant).
3. Community logs (resident-submitted trees) are planned for a later phase and are not shown yet.

## How it is built

Static front end, no backend, no API keys.

- `app/` - the site. MapLibre GL JS (free basemaps: CARTO, OpenStreetMap, Esri imagery). `index.html`, `app.js`, and pre-processed overlays in `app/data/`.
- `convert.py` - offline data step. Reads the real public rasters over the network, clips them to Larkana's bounding box `[68.10, 27.47, 68.33, 27.65]`, and writes web overlays (`canopy.png`, `landcover.png`, `heat.png`) plus `meta.json` with bounds, capture dates, and honest stats. Uses decimated windowed reads so only a few hundred KB come down per source.
- `DATA_SOURCES.md` - every source, licence, and URL, each verified live.

### Rebuild the data

```
pip install rasterio numpy pillow requests
python convert.py
```

### Run locally

```
cd app
python -m http.server 8848
# open http://localhost:8848
```

## Roadmap

- Phase 1 (this): self-sufficient map plus planting guide. Low maintenance.
- Phase 2: residents log trees they plant (type, age, photo, location), a contributions map, and a leaderboard. Needs a backend, auth, photo storage, and moderation. Air quality also lands here: Larkana has no public ground sensor, so the plan is a modeled city AQI readout (IQAir or WAQI, clearly labeled), a coarse Sentinel-5P NO2 proxy layer, and, longer term, a low-cost community sensor network.
- Phase 3: change over time, partnerships, expansion beyond Larkana.

## Credits

Data: Meta and WRI, ESA WorldCover, USGS/NASA Landsat via Microsoft Planetary Computer, OpenStreetMap, CARTO, Esri. Built by Dr. Safeer Ali Mirani.

Species and planting advice is general guidance, not a substitute for local horticultural or arborist input. Do a trademark check before any official launch of the name.
